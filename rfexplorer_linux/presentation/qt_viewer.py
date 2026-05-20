from __future__ import annotations

import queue
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

from rfexplorer_linux.application.viewer_state import ViewerStateService
from rfexplorer_linux.config import (
    APP_TITLE,
    DEFAULT_BAUD,
    DEFAULT_BOTTOM_DBM,
    DEFAULT_HISTORY_REDRAW_SEC,
    DEFAULT_HISTORY_DEPTH,
    DEFAULT_PEAK_TIMEOUT_SEC,
    DEFAULT_TOP_DBM,
    MAX_HISTORY_DEPTH,
)
from rfexplorer_linux.domain.models import AnalyzerConfiguration, SweepSnapshot
from rfexplorer_linux.domain.services import build_history_surface_data
from rfexplorer_linux.infrastructure.rfexplorer_gateway import RFExplorerGateway


def run_gui(default_port: str | None) -> int:
    try:
        from PyQt5 import QtCore, QtGui, QtWidgets

        header_stretch = QtWidgets.QHeaderView.Stretch
        no_edit_triggers = QtWidgets.QAbstractItemView.NoEditTriggers
        no_selection = QtWidgets.QAbstractItemView.NoSelection
        horizontal_orientation = QtCore.Qt.Horizontal
        vertical_orientation = QtCore.Qt.Vertical
    except ImportError:
        from PyQt6 import QtCore, QtGui, QtWidgets

        header_stretch = QtWidgets.QHeaderView.ResizeMode.Stretch
        no_edit_triggers = QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
        no_selection = QtWidgets.QAbstractItemView.SelectionMode.NoSelection
        horizontal_orientation = QtCore.Qt.Orientation.Horizontal
        vertical_orientation = QtCore.Qt.Orientation.Vertical

    if not hasattr(QtCore, "Signal"):
        QtCore.Signal = QtCore.pyqtSignal  # type: ignore[attr-defined]

    from matplotlib import cm, colors
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.collections import LineCollection
    from matplotlib.figure import Figure
    from matplotlib.patches import Polygon

    class RFExplorerWorker(QtCore.QThread):
        status_changed = QtCore.Signal(str)
        device_ready = QtCore.Signal(object)
        connection_changed = QtCore.Signal(bool)
        sweep_ready = QtCore.Signal(object)
        error_raised = QtCore.Signal(str)

        def __init__(self, parent: QtCore.QObject | None = None) -> None:
            super().__init__(parent)
            self._running = True
            self._commands: "queue.SimpleQueue[tuple[str, dict[str, Any]]]" = queue.SimpleQueue()
            self._gateway = RFExplorerGateway(baud_rate=DEFAULT_BAUD)

        def stop(self) -> None:
            self._running = False
            self._commands.put(("disconnect", {}))

        def connect_device(self, port: str | None) -> None:
            self._commands.put(("connect", {"port": port}))

        def disconnect_device(self) -> None:
            self._commands.put(("disconnect", {}))

        def apply_range(self, start_mhz: float, stop_mhz: float, top_dbm: float, bottom_dbm: float) -> None:
            self._commands.put(
                (
                    "range",
                    {
                        "start_mhz": start_mhz,
                        "stop_mhz": stop_mhz,
                        "top_dbm": top_dbm,
                        "bottom_dbm": bottom_dbm,
                    },
                )
            )

        def set_max_hold(self, enabled: bool) -> None:
            self._commands.put(("max_hold", {"enabled": enabled}))

        def request_refresh(self) -> None:
            self._commands.put(("refresh", {}))

        def run(self) -> None:
            while self._running:
                self._drain_commands()
                self._poll_snapshot()
                self.msleep(40)
            self._close_device()

        def _drain_commands(self) -> None:
            while True:
                try:
                    command, payload = self._commands.get_nowait()
                except queue.Empty:
                    return

                try:
                    if command == "connect":
                        self._open_device(payload.get("port"))
                    elif command == "disconnect":
                        self._close_device()
                    elif command == "range":
                        self._apply_range(**payload)
                    elif command == "max_hold":
                        self._toggle_max_hold(bool(payload.get("enabled")))
                    elif command == "refresh":
                        self._refresh_config()
                except Exception as exc:
                    self.error_raised.emit(str(exc))
                    self.status_changed.emit(f"Erro: {exc}")

        def _open_device(self, port: str | None) -> None:
            self._close_device()
            self.status_changed.emit("Localizando porta do RF Explorer...")
            port_value = port.strip() if port else None
            if port_value == "":
                port_value = None
            configuration = self._gateway.connect(port_value)
            self.connection_changed.emit(True)
            self.status_changed.emit(
                f"Conectado em {configuration.port} com firmware {configuration.firmware}."
            )
            self.device_ready.emit(configuration)
            self._refresh_config()

        def _close_device(self) -> None:
            if self._gateway.is_connected:
                self._gateway.disconnect()
                self.connection_changed.emit(False)
                self.status_changed.emit("Desconectado.")

        def _refresh_config(self) -> None:
            if not self._gateway.is_connected:
                return
            self.device_ready.emit(self._gateway.refresh_configuration())

        def _apply_range(self, start_mhz: float, stop_mhz: float, top_dbm: float, bottom_dbm: float) -> None:
            if stop_mhz <= start_mhz:
                raise RuntimeError("A frequencia final precisa ser maior que a inicial.")
            configuration = self._gateway.apply_range(start_mhz, stop_mhz, top_dbm, bottom_dbm)
            self.device_ready.emit(configuration)
            self.status_changed.emit(f"Range aplicado: {start_mhz:.3f} MHz ate {stop_mhz:.3f} MHz.")

        def _toggle_max_hold(self, enabled: bool) -> None:
            self._gateway.set_max_hold(enabled)
            self.status_changed.emit("Max hold ativado." if enabled else "Max hold desativado.")

        def _poll_snapshot(self) -> None:
            snapshot = self._gateway.read_next_snapshot()
            if snapshot is not None:
                self.sweep_ready.emit(snapshot)

    class MainWindow(QtWidgets.QMainWindow):
        def __init__(self, initial_port: str | None = None) -> None:
            super().__init__()
            self.setWindowTitle(APP_TITLE)
            self.resize(1680, 980)
            self._default_port = initial_port or "/dev/ttyUSB0"
            self._state_service = ViewerStateService(
                peak_timeout_sec=DEFAULT_PEAK_TIMEOUT_SEC,
                history_depth=DEFAULT_HISTORY_DEPTH,
            )
            self._last_history_redraw_epoch = 0.0

            self._worker = RFExplorerWorker(self)
            self._worker.status_changed.connect(self._set_status)
            self._worker.connection_changed.connect(self._on_connection_changed)
            self._worker.device_ready.connect(self._on_device_ready)
            self._worker.sweep_ready.connect(self._on_sweep_ready)
            self._worker.error_raised.connect(self._on_worker_error)
            self._worker.start()

            self._build_ui()
            self._set_status("Pronto para conectar.")

            self._transient_timer = QtCore.QTimer(self)
            self._transient_timer.setInterval(250)
            self._transient_timer.timeout.connect(self._refresh_transient_state)
            self._transient_timer.start()

            QtCore.QTimer.singleShot(250, self._auto_connect_if_possible)

        def closeEvent(self, event: QtGui.QCloseEvent) -> None:
            self._worker.stop()
            self._worker.wait(4000)
            super().closeEvent(event)

        def _build_ui(self) -> None:
            root = QtWidgets.QWidget()
            self.setCentralWidget(root)

            layout = QtWidgets.QVBoxLayout(root)
            layout.setContentsMargins(12, 12, 12, 12)
            layout.setSpacing(10)

            top_bar = QtWidgets.QFrame()
            top_bar.setFrameShape(QtWidgets.QFrame.StyledPanel)
            top_layout = QtWidgets.QGridLayout(top_bar)
            top_layout.setHorizontalSpacing(10)
            top_layout.setVerticalSpacing(8)

            self.port_edit = QtWidgets.QLineEdit(self._default_port)
            self.port_edit.setPlaceholderText("/dev/ttyUSB0 ou vazio para autodetect")
            self.connect_button = QtWidgets.QPushButton("Conectar")
            self.disconnect_button = QtWidgets.QPushButton("Desconectar")
            self.disconnect_button.setEnabled(False)
            self.refresh_button = QtWidgets.QPushButton("Atualizar Config")
            self.refresh_button.setEnabled(False)

            self.start_spin = self._freq_spin()
            self.stop_spin = self._freq_spin()
            self.top_dbm_spin = self._dbm_spin(DEFAULT_TOP_DBM)
            self.bottom_dbm_spin = self._dbm_spin(DEFAULT_BOTTOM_DBM)
            self.apply_range_button = QtWidgets.QPushButton("Aplicar Range")
            self.apply_range_button.setEnabled(False)
            self.max_hold_checkbox = QtWidgets.QCheckBox("Max Hold")
            self.max_hold_checkbox.setEnabled(False)

            self.peak_timeout_spin = QtWidgets.QDoubleSpinBox()
            self.peak_timeout_spin.setRange(0.2, 30.0)
            self.peak_timeout_spin.setDecimals(1)
            self.peak_timeout_spin.setSingleStep(0.5)
            self.peak_timeout_spin.setValue(DEFAULT_PEAK_TIMEOUT_SEC)
            self.peak_timeout_spin.setSuffix(" s")

            self.history_depth_spin = QtWidgets.QSpinBox()
            self.history_depth_spin.setRange(10, MAX_HISTORY_DEPTH)
            self.history_depth_spin.setValue(DEFAULT_HISTORY_DEPTH)
            self.history_depth_spin.setSuffix(" sweeps")

            self.info_label = QtWidgets.QLabel("Sem conexao")
            self.info_label.setStyleSheet("font-weight: 600;")

            top_layout.addWidget(QtWidgets.QLabel("Porta"), 0, 0)
            top_layout.addWidget(self.port_edit, 0, 1, 1, 3)
            top_layout.addWidget(self.connect_button, 0, 4)
            top_layout.addWidget(self.disconnect_button, 0, 5)
            top_layout.addWidget(self.refresh_button, 0, 6)

            top_layout.addWidget(QtWidgets.QLabel("Inicio (MHz)"), 1, 0)
            top_layout.addWidget(self.start_spin, 1, 1)
            top_layout.addWidget(QtWidgets.QLabel("Fim (MHz)"), 1, 2)
            top_layout.addWidget(self.stop_spin, 1, 3)
            top_layout.addWidget(self.apply_range_button, 1, 4)
            top_layout.addWidget(self.max_hold_checkbox, 1, 5)

            top_layout.addWidget(QtWidgets.QLabel("Topo (dBm)"), 2, 0)
            top_layout.addWidget(self.top_dbm_spin, 2, 1)
            top_layout.addWidget(QtWidgets.QLabel("Base (dBm)"), 2, 2)
            top_layout.addWidget(self.bottom_dbm_spin, 2, 3)
            top_layout.addWidget(self.info_label, 2, 4, 1, 3)

            top_layout.addWidget(QtWidgets.QLabel("Timeout pico"), 3, 0)
            top_layout.addWidget(self.peak_timeout_spin, 3, 1)
            top_layout.addWidget(QtWidgets.QLabel("Historico 3D"), 3, 2)
            top_layout.addWidget(self.history_depth_spin, 3, 3)

            layout.addWidget(top_bar)

            content = QtWidgets.QSplitter(horizontal_orientation)
            layout.addWidget(content, 1)

            left_panel = QtWidgets.QSplitter(vertical_orientation)
            content.addWidget(left_panel)

            spectrum_card = QtWidgets.QGroupBox("Espectro Atual")
            spectrum_layout = QtWidgets.QVBoxLayout(spectrum_card)
            spectrum_layout.setContentsMargins(8, 8, 8, 8)
            self.spectrum_figure = Figure(facecolor="#06080d")
            self.spectrum_canvas = FigureCanvas(self.spectrum_figure)
            self.spectrum_axis = self.spectrum_figure.add_subplot(111)
            spectrum_layout.addWidget(self.spectrum_canvas)
            left_panel.addWidget(spectrum_card)

            history_card = QtWidgets.QGroupBox("Historico 3D")
            history_layout = QtWidgets.QVBoxLayout(history_card)
            history_layout.setContentsMargins(8, 8, 8, 8)
            self.history_figure = Figure(facecolor="#06080d")
            self.history_canvas = FigureCanvas(self.history_figure)
            self.history_axis = self.history_figure.add_subplot(111, projection="3d")
            history_layout.addWidget(self.history_canvas)
            left_panel.addWidget(history_card)
            left_panel.setSizes([540, 360])

            right_panel = QtWidgets.QWidget()
            right_layout = QtWidgets.QVBoxLayout(right_panel)
            right_layout.setContentsMargins(8, 8, 8, 8)
            right_layout.setSpacing(10)

            stats_group = QtWidgets.QGroupBox("Leitura Atual")
            stats_layout = QtWidgets.QFormLayout(stats_group)
            self.peak_value_label = QtWidgets.QLabel("--")
            self.noise_floor_label = QtWidgets.QLabel("--")
            self.sweep_count_label = QtWidgets.QLabel("--")
            self.capture_time_label = QtWidgets.QLabel("--")
            self.active_peaks_label = QtWidgets.QLabel("--")
            stats_layout.addRow("Pico mais forte", self.peak_value_label)
            stats_layout.addRow("Piso de ruido", self.noise_floor_label)
            stats_layout.addRow("Sweeps recebidos", self.sweep_count_label)
            stats_layout.addRow("Ultima captura", self.capture_time_label)
            stats_layout.addRow("Picos visiveis", self.active_peaks_label)
            right_layout.addWidget(stats_group)

            peaks_group = QtWidgets.QGroupBox("Picos Ativos")
            peaks_layout = QtWidgets.QVBoxLayout(peaks_group)
            self.peaks_table = QtWidgets.QTableWidget(0, 3)
            self.peaks_table.setHorizontalHeaderLabels(["Frequencia (MHz)", "Amplitude (dBm)", "Idade (s)"])
            self.peaks_table.horizontalHeader().setSectionResizeMode(header_stretch)
            self.peaks_table.verticalHeader().setVisible(False)
            self.peaks_table.setEditTriggers(no_edit_triggers)
            self.peaks_table.setSelectionMode(no_selection)
            peaks_layout.addWidget(self.peaks_table)
            right_layout.addWidget(peaks_group, 1)

            status_group = QtWidgets.QGroupBox("Status")
            status_layout = QtWidgets.QVBoxLayout(status_group)
            self.status_box = QtWidgets.QPlainTextEdit()
            self.status_box.setReadOnly(True)
            self.status_box.setMaximumBlockCount(250)
            status_layout.addWidget(self.status_box)
            right_layout.addWidget(status_group, 1)

            content.addWidget(right_panel)
            content.setSizes([1180, 420])

            self.connect_button.clicked.connect(self._connect_clicked)
            self.disconnect_button.clicked.connect(self._worker.disconnect_device)
            self.refresh_button.clicked.connect(self._worker.request_refresh)
            self.apply_range_button.clicked.connect(self._apply_range_clicked)
            self.max_hold_checkbox.toggled.connect(self._worker.set_max_hold)
            self.peak_timeout_spin.valueChanged.connect(self._on_peak_timeout_changed)
            self.history_depth_spin.valueChanged.connect(self._on_history_depth_changed)

            self._render_spectrum()
            self._render_history()

        def _freq_spin(self) -> Any:
            spin = QtWidgets.QDoubleSpinBox()
            spin.setDecimals(3)
            spin.setRange(0.0, 10000.0)
            spin.setSingleStep(0.1)
            spin.setSuffix(" MHz")
            return spin

        def _dbm_spin(self, default: float) -> Any:
            spin = QtWidgets.QDoubleSpinBox()
            spin.setDecimals(1)
            spin.setRange(-200.0, 100.0)
            spin.setSingleStep(5.0)
            spin.setValue(default)
            spin.setSuffix(" dBm")
            return spin

        def _connect_clicked(self) -> None:
            port_text = self.port_edit.text().strip()
            self._worker.connect_device(port_text or None)
            self._set_status(f"Tentando conectar em {port_text or 'autodetect'}...")

        def _auto_connect_if_possible(self) -> None:
            if self.port_edit.text().strip() or Path("/dev/ttyUSB0").exists():
                self._connect_clicked()

        def _apply_range_clicked(self) -> None:
            self._worker.apply_range(
                self.start_spin.value(),
                self.stop_spin.value(),
                self.top_dbm_spin.value(),
                self.bottom_dbm_spin.value(),
            )

        def _on_connection_changed(self, connected: bool) -> None:
            self.connect_button.setEnabled(not connected)
            self.disconnect_button.setEnabled(connected)
            self.refresh_button.setEnabled(connected)
            self.apply_range_button.setEnabled(connected)
            self.max_hold_checkbox.setEnabled(connected)
            if not connected:
                self._state_service.reset()
                self._render_spectrum()
                self._render_history()
                self._refresh_peaks_table()

        def _on_device_ready(self, configuration: AnalyzerConfiguration) -> None:
            self.start_spin.blockSignals(True)
            self.stop_spin.blockSignals(True)
            self.top_dbm_spin.blockSignals(True)
            self.bottom_dbm_spin.blockSignals(True)
            self.start_spin.setRange(configuration.min_freq_mhz, configuration.max_freq_mhz)
            self.stop_spin.setRange(configuration.min_freq_mhz, configuration.max_freq_mhz)
            self.start_spin.setValue(configuration.start_mhz)
            self.stop_spin.setValue(configuration.stop_mhz)
            self.top_dbm_spin.setValue(configuration.top_dbm)
            self.bottom_dbm_spin.setValue(configuration.bottom_dbm)
            self.start_spin.blockSignals(False)
            self.stop_spin.blockSignals(False)
            self.top_dbm_spin.blockSignals(False)
            self.bottom_dbm_spin.blockSignals(False)

            self.info_label.setText(
                f"{configuration.main_model} + {configuration.expansion_model} | "
                f"{configuration.firmware} | {configuration.serial} | {configuration.port}"
            )
            self._render_spectrum()
            self._render_history()

        def _on_sweep_ready(self, snapshot: SweepSnapshot) -> None:
            state = self._state_service.ingest_snapshot(snapshot)
            self._render_spectrum()
            if snapshot.capture_epoch - self._last_history_redraw_epoch >= DEFAULT_HISTORY_REDRAW_SEC:
                self._render_history()
                self._last_history_redraw_epoch = snapshot.capture_epoch
            self._refresh_stats(state.last_snapshot)
            self._refresh_peaks_table()

        def _on_worker_error(self, message: str) -> None:
            QtWidgets.QMessageBox.warning(self, APP_TITLE, message)
            self._set_status(f"Erro: {message}")

        def _on_peak_timeout_changed(self, value: float) -> None:
            self._state_service.set_peak_timeout(value, now_epoch=time.time())
            self._render_spectrum()
            self._refresh_peaks_table()
            self._set_status(f"Timeout dos picos ajustado para {value:.1f}s.")

        def _on_history_depth_changed(self, value: int) -> None:
            self._state_service.set_history_depth(value)
            self._render_history()
            self._set_status(f"Historico 3D ajustado para {value} sweeps.")

        def _refresh_transient_state(self) -> None:
            state, changed = self._state_service.expire_peaks(time.time())
            if not changed:
                return
            if state.last_snapshot is not None:
                self._render_spectrum()
            self._refresh_peaks_table()

        def _refresh_stats(self, snapshot: SweepSnapshot | None) -> None:
            if snapshot is None:
                self.peak_value_label.setText("--")
                self.noise_floor_label.setText("--")
                self.sweep_count_label.setText("--")
                self.capture_time_label.setText("--")
                self.active_peaks_label.setText("0")
                return
            self.peak_value_label.setText(f"{snapshot.peak_mhz:.3f} MHz @ {snapshot.peak_dbm:.1f} dBm")
            self.noise_floor_label.setText(f"{snapshot.noise_floor_dbm:.1f} dBm")
            self.sweep_count_label.setText(str(snapshot.sweep_count))
            self.capture_time_label.setText(time.strftime("%H:%M:%S", time.localtime(snapshot.capture_epoch)))
            self.active_peaks_label.setText(str(len(self._state_service.state.tracked_peaks)))

        def _refresh_peaks_table(self) -> None:
            tracked_peaks = self._state_service.state.tracked_peaks
            self.peaks_table.setRowCount(len(tracked_peaks))
            now_epoch = time.time()
            for row, peak in enumerate(tracked_peaks):
                age_sec = max(0.0, now_epoch - peak.last_seen_epoch)
                self.peaks_table.setItem(row, 0, QtWidgets.QTableWidgetItem(f"{peak.frequency_mhz:.3f}"))
                self.peaks_table.setItem(row, 1, QtWidgets.QTableWidgetItem(f"{peak.amplitude_dbm:.1f}"))
                self.peaks_table.setItem(row, 2, QtWidgets.QTableWidgetItem(f"{age_sec:.1f}"))
            self.active_peaks_label.setText(str(len(tracked_peaks)))

        def _style_2d_axis(self) -> None:
            axis = self.spectrum_axis
            axis.clear()
            axis.set_facecolor("#06080d")
            axis.tick_params(colors="#d9e2ef")
            axis.spines["bottom"].set_color("#475569")
            axis.spines["top"].set_color("#475569")
            axis.spines["left"].set_color("#475569")
            axis.spines["right"].set_color("#475569")
            axis.xaxis.label.set_color("#d9e2ef")
            axis.yaxis.label.set_color("#d9e2ef")
            axis.title.set_color("#f8fafc")
            axis.grid(color="#334155", alpha=0.30, linewidth=0.8)

        def _render_spectrum(self) -> None:
            self._style_2d_axis()
            axis = self.spectrum_axis
            bottom_dbm = self.bottom_dbm_spin.value()
            top_dbm = self.top_dbm_spin.value()
            axis.set_xlabel("Frequencia (MHz)")
            axis.set_ylabel("Amplitude (dBm)")

            snapshot = self._state_service.state.last_snapshot
            if snapshot is None:
                axis.set_title("Spectrum ao vivo")
                axis.text(
                    0.5,
                    0.5,
                    "Aguardando sweeps do RF Explorer",
                    color="#94a3b8",
                    ha="center",
                    va="center",
                    transform=axis.transAxes,
                )
                self.spectrum_canvas.draw_idle()
                return

            frequencies = np.asarray(snapshot.frequencies_mhz, dtype=float)
            amplitudes = np.asarray(snapshot.amplitudes_dbm, dtype=float)
            axis.set_xlim(float(frequencies.min()), float(frequencies.max()))
            axis.set_ylim(bottom_dbm, top_dbm)
            axis.set_title("Spectrum ao vivo com gradiente")

            gradient = np.linspace(0.0, 1.0, 512, dtype=float).reshape(512, 1)
            image = axis.imshow(
                gradient,
                extent=(float(frequencies.min()), float(frequencies.max()), bottom_dbm, top_dbm),
                aspect="auto",
                origin="lower",
                cmap=cm.plasma,
                alpha=0.80,
                interpolation="bicubic",
                zorder=1,
            )
            clip_points = np.vstack(
                (
                    np.array([[frequencies[0], bottom_dbm]]),
                    np.column_stack((frequencies, amplitudes)),
                    np.array([[frequencies[-1], bottom_dbm]]),
                )
            )
            image.set_clip_path(Polygon(clip_points, closed=True, transform=axis.transData))
            axis.fill_between(frequencies, amplitudes, bottom_dbm, color="#0f172a", alpha=0.35, zorder=1)

            if len(frequencies) > 1:
                points = np.array([frequencies, amplitudes]).T.reshape(-1, 1, 2)
                segments = np.concatenate([points[:-1], points[1:]], axis=1)
                norm = colors.Normalize(vmin=bottom_dbm, vmax=top_dbm)
                collection = LineCollection(segments, cmap=cm.plasma, norm=norm)
                collection.set_array(amplitudes[:-1])
                collection.set_linewidth(2.4)
                collection.set_zorder(3)
                axis.add_collection(collection)
            else:
                axis.plot(frequencies, amplitudes, color="#f472b6", linewidth=2.0, zorder=3)

            axis.axhline(
                snapshot.noise_floor_dbm,
                color="#86efac",
                linestyle="--",
                linewidth=1.0,
                alpha=0.85,
                zorder=2,
            )

            tracked_peaks = self._state_service.state.tracked_peaks
            if tracked_peaks:
                now_epoch = time.time()
                timeout_sec = max(0.1, self.peak_timeout_spin.value())
                peak_freqs = [peak.frequency_mhz for peak in tracked_peaks]
                peak_amps = [peak.amplitude_dbm for peak in tracked_peaks]
                axis.scatter(
                    peak_freqs,
                    peak_amps,
                    color="#fde047",
                    edgecolors="#fff7ae",
                    linewidths=0.9,
                    s=46,
                    zorder=5,
                )
                for peak in tracked_peaks:
                    age_ratio = min(1.0, max(0.0, (now_epoch - peak.last_seen_epoch) / timeout_sec))
                    alpha = max(0.28, 1.0 - age_ratio)
                    axis.annotate(
                        f"{peak.frequency_mhz:.3f} MHz\n{peak.amplitude_dbm:.1f} dBm",
                        xy=(peak.frequency_mhz, peak.amplitude_dbm),
                        xytext=(0, 12),
                        textcoords="offset points",
                        ha="center",
                        va="bottom",
                        color=(1.0, 0.95, 0.75, alpha),
                        fontsize=8.5,
                        bbox={
                            "boxstyle": "round,pad=0.25",
                            "fc": (0.07, 0.10, 0.14, 0.75 * alpha),
                            "ec": (1.0, 0.95, 0.75, 0.40 * alpha),
                        },
                        zorder=6,
                    )

            axis.text(
                0.012,
                0.97,
                f"Piso de ruido {snapshot.noise_floor_dbm:.1f} dBm",
                color="#d8f5dd",
                fontsize=9,
                ha="left",
                va="top",
                transform=axis.transAxes,
            )
            self.spectrum_canvas.draw_idle()

        def _style_3d_axis(self) -> None:
            axis = self.history_axis
            axis.clear()
            axis.set_facecolor("#06080d")
            axis.tick_params(colors="#d9e2ef")
            axis.xaxis.line.set_color("#475569")
            axis.yaxis.line.set_color("#475569")
            axis.zaxis.line.set_color("#475569")
            axis.xaxis.label.set_color("#d9e2ef")
            axis.yaxis.label.set_color("#d9e2ef")
            axis.zaxis.label.set_color("#d9e2ef")
            axis.title.set_color("#f8fafc")
            axis.view_init(elev=26, azim=-120)

            for axis_info in (axis.xaxis, axis.yaxis, axis.zaxis):
                axis_info._axinfo["grid"]["color"] = (0.34, 0.40, 0.49, 0.22)

            axis.xaxis.pane.set_facecolor((0.03, 0.04, 0.06, 1.0))
            axis.yaxis.pane.set_facecolor((0.03, 0.04, 0.06, 1.0))
            axis.zaxis.pane.set_facecolor((0.03, 0.04, 0.06, 1.0))

        def _render_history(self) -> None:
            self._style_3d_axis()
            axis = self.history_axis
            axis.set_title("Waterfall historico 3D")
            axis.set_xlabel("Frequencia (MHz)", labelpad=10)
            axis.set_ylabel("Tempo", labelpad=10)
            axis.set_zlabel("Amplitude (dBm)", labelpad=8)

            history = self._state_service.state.history
            if len(history) < 2:
                axis.text2D(
                    0.5,
                    0.5,
                    "Aguardando historico suficiente",
                    color="#94a3b8",
                    ha="center",
                    va="center",
                    transform=axis.transAxes,
                )
                self.history_canvas.draw_idle()
                return

            frequencies, sweep_indexes, amplitude_grid = build_history_surface_data(history)
            freq_grid, time_grid = np.meshgrid(frequencies, sweep_indexes)
            bottom_dbm = self.bottom_dbm_spin.value()
            top_dbm = self.top_dbm_spin.value()
            norm = colors.Normalize(vmin=bottom_dbm, vmax=top_dbm)
            face_colors = cm.plasma(norm(amplitude_grid))

            axis.plot_surface(
                freq_grid,
                time_grid,
                amplitude_grid,
                facecolors=face_colors,
                rstride=1,
                cstride=max(1, len(frequencies) // 150),
                linewidth=0,
                antialiased=False,
                shade=False,
                alpha=0.98,
            )

            ridge_step = max(1, len(history) // 7)
            for idx in range(0, len(history), ridge_step):
                axis.plot(
                    frequencies,
                    np.full_like(frequencies, sweep_indexes[idx]),
                    amplitude_grid[idx],
                    color=(0.95, 0.30, 0.88, 0.35 + (idx / max(1, len(history) - 1)) * 0.35),
                    linewidth=0.8,
                )

            axis.plot(
                frequencies,
                np.full_like(frequencies, sweep_indexes[-1]),
                amplitude_grid[-1],
                color="#fef08a",
                linewidth=1.8,
            )

            axis.set_xlim(float(frequencies.min()), float(frequencies.max()))
            axis.set_ylim(float(sweep_indexes[-1]), float(sweep_indexes[0]))
            axis.set_zlim(bottom_dbm, top_dbm)

            if len(history) >= 3:
                mid_index = len(history) // 2
                axis.set_yticks([sweep_indexes[0], sweep_indexes[mid_index], sweep_indexes[-1]])
                axis.set_yticklabels(
                    [
                        time.strftime("%H:%M:%S", time.localtime(history[0].capture_epoch)),
                        time.strftime("%H:%M:%S", time.localtime(history[mid_index].capture_epoch)),
                        time.strftime("%H:%M:%S", time.localtime(history[-1].capture_epoch)),
                    ]
                )

            self.history_canvas.draw_idle()

        def _set_status(self, message: str) -> None:
            timestamp = time.strftime("%H:%M:%S")
            self.status_box.appendPlainText(f"[{timestamp}] {message}")

    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setWindowIcon(QtGui.QIcon.fromTheme("applications-science"))

    window = MainWindow(default_port)
    window.show()
    return app.exec()

