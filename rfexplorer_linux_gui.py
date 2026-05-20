#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import io
import os
import queue
import serial
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parent


def resolve_rfexplorer_lib() -> Path | None:
    candidates = [
        Path.cwd() / "vendor" / "RFExplorer-for-Python",
        PROJECT_DIR / "vendor" / "RFExplorer-for-Python",
    ]

    env_path = Path(
        os.environ.get(
            "RFEXPLORER_LIB",
            "",
        )
    )
    if str(env_path):
        candidates.insert(0, env_path.expanduser())

    for candidate in candidates:
        if (candidate / "RFExplorer" / "RFExplorer.py").exists():
            return candidate

    return None


RFEXPLORER_LIB = resolve_rfexplorer_lib()
if RFEXPLORER_LIB is not None and str(RFEXPLORER_LIB) not in sys.path:
    sys.path.insert(0, str(RFEXPLORER_LIB))

RFEXPLORER_IMPORT_ERROR: RuntimeError | None = None
try:
    import RFExplorer  # type: ignore
except ModuleNotFoundError:
    RFExplorer = None  # type: ignore[assignment]
    RFEXPLORER_IMPORT_ERROR = RuntimeError(
        "Biblioteca RFExplorer-for-Python nao encontrada. "
        "Defina RFEXPLORER_LIB ou clone a biblioteca em vendor/RFExplorer-for-Python."
    )


APP_TITLE = "RF Explorer Linux Viewer"
DEFAULT_BAUD = 500000
DEFAULT_TOP_DBM = 0
DEFAULT_BOTTOM_DBM = -120
MAX_PEAKS = 5


@dataclass
class SweepSnapshot:
    frequencies_mhz: list[float]
    amplitudes_dbm: list[float]
    top_peaks: list[tuple[float, float]]
    noise_floor_dbm: float
    peak_dbm: float
    peak_mhz: float
    capture_epoch: float
    sweep_count: int


def extract_top_peaks(
    frequencies: list[float], amplitudes: list[float], max_peaks: int
) -> list[tuple[float, float]]:
    candidates: list[tuple[float, float]] = []
    if len(amplitudes) < 3:
        return list(zip(frequencies, amplitudes))[:max_peaks]

    for idx in range(1, len(amplitudes) - 1):
        left = amplitudes[idx - 1]
        center = amplitudes[idx]
        right = amplitudes[idx + 1]
        if center >= left and center >= right:
            candidates.append((frequencies[idx], center))

    if not candidates:
        peak_index = max(range(len(amplitudes)), key=amplitudes.__getitem__)
        return [(frequencies[peak_index], amplitudes[peak_index])]

    selected: list[tuple[float, float]] = []
    for freq_mhz, dbm in sorted(candidates, key=lambda item: item[1], reverse=True):
        if all(abs(freq_mhz - existing_freq) > 0.25 for existing_freq, _ in selected):
            selected.append((freq_mhz, dbm))
        if len(selected) >= max_peaks:
            break
    return selected


def estimate_noise_floor(amplitudes: list[float]) -> float:
    if not amplitudes:
        return -120.0
    ordered = sorted(amplitudes)
    return ordered[max(0, int(len(ordered) * 0.2) - 1)]


def snapshot_from_sweep(sweep: Any, sweep_count: int) -> SweepSnapshot:
    total_points = sweep.TotalDataPoints
    frequencies = [sweep.GetFrequencyMHZ(i) for i in range(total_points)]
    amplitudes = [sweep.GetAmplitude_DBM(i) for i in range(total_points)]
    top_peaks = extract_top_peaks(frequencies, amplitudes, MAX_PEAKS)
    noise_floor = estimate_noise_floor(amplitudes)
    peak_mhz, peak_dbm = top_peaks[0] if top_peaks else (0.0, -120.0)
    return SweepSnapshot(
        frequencies_mhz=frequencies,
        amplitudes_dbm=amplitudes,
        top_peaks=top_peaks,
        noise_floor_dbm=noise_floor,
        peak_dbm=peak_dbm,
        peak_mhz=peak_mhz,
        capture_epoch=time.time(),
        sweep_count=sweep_count,
    )


def open_rfe(port: str | None, baud_rate: int = DEFAULT_BAUD) -> Any:
    if RFEXPLORER_IMPORT_ERROR is not None:
        raise RFEXPLORER_IMPORT_ERROR

    if port:
        try:
            probe = serial.Serial(port, baud_rate, timeout=0.2)
            probe.close()
        except serial.SerialException as exc:
            if "Permission denied" in str(exc):
                raise RuntimeError(
                    f"Sem permissao para abrir {port}. "
                    "Abra pelo launcher 'RF Explorer Linux Viewer' para pedir acesso, "
                    "ou adicione o usuario ao grupo dialout."
                ) from exc
            raise

    rfe = RFExplorer.RFECommunicator()
    rfe.VerboseLevel = 0
    rfe.UseByteBLOB = False
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        rfe.GetConnectedPorts()
    if not rfe.ConnectPort(port, baud_rate):
        rfe.Close()
        raise RuntimeError("Nao foi possivel conectar ao RF Explorer.")

    deadline = time.time() + 8.0
    while time.time() < deadline and rfe.ActiveModel == RFExplorer.RFE_Common.eModel.MODEL_NONE:
        rfe.ProcessReceivedString(True)
        time.sleep(0.05)

    if rfe.ActiveModel == RFExplorer.RFE_Common.eModel.MODEL_NONE:
        rfe.Close()
        raise RuntimeError("Conectou na serial, mas nao recebeu a identificacao do analyzer.")

    rfe.CleanSweepData()
    return rfe


def refresh_config(rfe: Any) -> None:
    rfe.SendCommand_RequestConfigData()
    deadline = time.time() + 2.0
    while time.time() < deadline:
        rfe.ProcessReceivedString(True)
        time.sleep(0.03)


def device_payload(rfe: Any) -> dict[str, Any]:
    return {
        "port": rfe.PortName,
        "firmware": rfe.RFExplorerFirmwareDetected,
        "serial": rfe.SerialNumber,
        "main_model": getattr(rfe.MainBoardModel, "name", str(rfe.MainBoardModel)),
        "expansion_model": getattr(rfe.ExpansionBoardModel, "name", str(rfe.ExpansionBoardModel)),
        "start_mhz": float(rfe.StartFrequencyMHZ),
        "stop_mhz": float(rfe.StopFrequencyMHZ),
        "top_dbm": float(rfe.AmplitudeTopDBM),
        "bottom_dbm": float(rfe.AmplitudeBottomDBM),
        "min_freq_mhz": float(rfe.MinFreqMHZ),
        "max_freq_mhz": float(rfe.MaxFreqMHZ),
        "min_span_mhz": float(rfe.MinSpanMHZ),
        "max_span_mhz": float(rfe.MaxSpanMHZ),
    }


def run_headless_smoke(port: str | None, duration_sec: float, start_mhz: float | None, stop_mhz: float | None) -> int:
    rfe = None
    try:
        rfe = open_rfe(port, DEFAULT_BAUD)
        if start_mhz is not None and stop_mhz is not None:
            rfe.UpdateDeviceConfig(start_mhz, stop_mhz, DEFAULT_TOP_DBM, DEFAULT_BOTTOM_DBM)
            time.sleep(1.0)
            refresh_config(rfe)

        print(
            f"Conectado: {rfe.MainBoardModel.name} + {rfe.ExpansionBoardModel.name} | "
            f"firmware {rfe.RFExplorerFirmwareDetected} | serial {rfe.SerialNumber}"
        )

        end_time = time.time() + duration_sec
        last_count = 0
        while time.time() < end_time:
            rfe.ProcessReceivedString(True)
            count = rfe.SweepData.Count
            if count > 0 and count != last_count:
                sweep = rfe.SweepData.GetData(count - 1)
                peak_idx = sweep.GetPeakDataPoint()
                print(
                    f"sweep={count} peak={sweep.GetFrequencyMHZ(peak_idx):.3f} MHz "
                    f"{sweep.GetAmplitude_DBM(peak_idx):.1f} dBm "
                    f"range={sweep.StartFrequencyMHZ:.3f}-{sweep.EndFrequencyMHZ:.3f} MHz"
                )
                last_count = count
            time.sleep(0.05)
        return 0
    finally:
        if rfe is not None:
            rfe.Close()


def run_gui(default_port: str | None) -> int:
    try:
        from PyQt5 import QtCore, QtGui, QtWidgets

        dash_line = QtCore.Qt.DashLine
        header_stretch = QtWidgets.QHeaderView.Stretch
        no_edit_triggers = QtWidgets.QAbstractItemView.NoEditTriggers
        no_selection = QtWidgets.QAbstractItemView.NoSelection
    except ImportError:
        from PyQt6 import QtCore, QtGui, QtWidgets

        dash_line = QtCore.Qt.PenStyle.DashLine
        header_stretch = QtWidgets.QHeaderView.ResizeMode.Stretch
        no_edit_triggers = QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
        no_selection = QtWidgets.QAbstractItemView.SelectionMode.NoSelection

    if not hasattr(QtCore, "Signal"):
        QtCore.Signal = QtCore.pyqtSignal  # type: ignore[attr-defined]

    import pyqtgraph as pg

    class RFExplorerWorker(QtCore.QThread):
        status_changed = QtCore.Signal(str)
        device_ready = QtCore.Signal(dict)
        connection_changed = QtCore.Signal(bool)
        sweep_ready = QtCore.Signal(object)
        error_raised = QtCore.Signal(str)

        def __init__(self, parent: QtCore.QObject | None = None) -> None:
            super().__init__(parent)
            self._running = True
            self._commands: "queue.SimpleQueue[tuple[str, dict[str, Any]]]" = queue.SimpleQueue()
            self._rfe = None
            self._last_sweep_count = 0

        def stop(self) -> None:
            self._running = False
            self._commands.put(("disconnect", {}))

        def connect_device(self, port: str | None, baud_rate: int = DEFAULT_BAUD) -> None:
            self._commands.put(("connect", {"port": port, "baud_rate": baud_rate}))

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
                self._poll_sweeps()
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
                        self._open_device(payload.get("port"), int(payload.get("baud_rate", DEFAULT_BAUD)))
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

        def _open_device(self, port: str | None, baud_rate: int) -> None:
            self._close_device()
            self.status_changed.emit("Localizando porta do RF Explorer...")
            port_value = port.strip() if port else None
            if port_value == "":
                port_value = None
            self._rfe = open_rfe(port_value, baud_rate)
            self._last_sweep_count = 0
            self.connection_changed.emit(True)
            self.status_changed.emit(
                f"Conectado em {self._rfe.PortName} com firmware {self._rfe.RFExplorerFirmwareDetected}."
            )
            self.device_ready.emit(device_payload(self._rfe))
            self._refresh_config()

        def _close_device(self) -> None:
            if self._rfe is not None:
                try:
                    self._rfe.Close()
                except Exception:
                    pass
                self._rfe = None
                self._last_sweep_count = 0
                self.connection_changed.emit(False)
                self.status_changed.emit("Desconectado.")

        def _refresh_config(self) -> None:
            if self._rfe is None:
                return
            refresh_config(self._rfe)
            self.device_ready.emit(device_payload(self._rfe))

        def _apply_range(self, start_mhz: float, stop_mhz: float, top_dbm: float, bottom_dbm: float) -> None:
            if self._rfe is None:
                return
            if stop_mhz <= start_mhz:
                raise RuntimeError("A frequencia final precisa ser maior que a inicial.")
            self._rfe.UpdateDeviceConfig(start_mhz, stop_mhz, top_dbm, bottom_dbm)
            time.sleep(0.8)
            self._rfe.CleanSweepData()
            self._last_sweep_count = 0
            self._refresh_config()
            self.status_changed.emit(f"Range aplicado: {start_mhz:.3f} MHz ate {stop_mhz:.3f} MHz.")

        def _toggle_max_hold(self, enabled: bool) -> None:
            if self._rfe is None:
                return
            self._rfe.UseMaxHold = enabled
            self.status_changed.emit("Max hold ativado." if enabled else "Max hold desativado.")

        def _poll_sweeps(self) -> None:
            if self._rfe is None or not self._rfe.PortConnected:
                return
            self._rfe.ProcessReceivedString(True)
            count = self._rfe.SweepData.Count
            if count <= 0 or count == self._last_sweep_count:
                return
            sweep = self._rfe.SweepData.GetData(count - 1)
            if sweep is None:
                return
            self._last_sweep_count = count
            self.sweep_ready.emit(snapshot_from_sweep(sweep, count))

    class MainWindow(QtWidgets.QMainWindow):
        def __init__(self, initial_port: str | None = None) -> None:
            super().__init__()
            self.setWindowTitle(APP_TITLE)
            self.resize(1420, 900)
            self._default_port = initial_port or "/dev/ttyUSB0"
            self._peak_labels: list[Any] = []

            self._worker = RFExplorerWorker(self)
            self._worker.status_changed.connect(self._set_status)
            self._worker.connection_changed.connect(self._on_connection_changed)
            self._worker.device_ready.connect(self._on_device_ready)
            self._worker.sweep_ready.connect(self._on_sweep_ready)
            self._worker.error_raised.connect(self._on_worker_error)
            self._worker.start()

            self._build_ui()
            self._set_status("Pronto para conectar.")
            QtCore.QTimer.singleShot(250, self._auto_connect_if_possible)

        def closeEvent(self, event: QtGui.QCloseEvent) -> None:
            self._worker.stop()
            self._worker.wait(4000)
            super().closeEvent(event)

        def _build_ui(self) -> None:
            root = QtWidgets.QWidget()
            self.setCentralWidget(root)

            layout = QtWidgets.QVBoxLayout(root)
            layout.setContentsMargins(14, 14, 14, 14)
            layout.setSpacing(12)

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

            layout.addWidget(top_bar)

            content = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
            layout.addWidget(content, 1)

            self.plot_widget = pg.PlotWidget(background="#101418")
            self.plot_widget.showGrid(x=True, y=True, alpha=0.2)
            self.plot_widget.setLabel("bottom", "Frequencia", units="MHz")
            self.plot_widget.setLabel("left", "Amplitude", units="dBm")
            self.plot_widget.addLegend(offset=(10, 10))
            self.plot_curve = self.plot_widget.plot(
                pen=pg.mkPen("#4fc3f7", width=2),
                name="Sweep Atual",
            )
            self.peak_scatter = pg.ScatterPlotItem(
                size=9,
                brush=pg.mkBrush("#ffb74d"),
                pen=pg.mkPen("#fff3e0", width=1),
                name="Picos",
            )
            self.plot_widget.addItem(self.peak_scatter)
            self.noise_floor_line = pg.InfiniteLine(
                angle=0,
                movable=False,
                pen=pg.mkPen("#81c784", width=1, style=dash_line),
                label="Ruido: {value:.1f} dBm",
                labelOpts={"position": 0.92, "color": "#c8e6c9", "fill": "#162018"},
            )
            self.plot_widget.addItem(self.noise_floor_line, ignoreBounds=True)
            self.crosshair_v = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#4dd0e1", style=dash_line))
            self.crosshair_h = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen("#4dd0e1", style=dash_line))
            self.plot_widget.addItem(self.crosshair_v, ignoreBounds=True)
            self.plot_widget.addItem(self.crosshair_h, ignoreBounds=True)
            self.mouse_label = QtWidgets.QLabel("Cursor: --")
            layout.addWidget(self.mouse_label)
            self.plot_widget.scene().sigMouseMoved.connect(self._on_mouse_moved)

            content.addWidget(self.plot_widget)

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
            stats_layout.addRow("Pico mais forte", self.peak_value_label)
            stats_layout.addRow("Piso de ruido", self.noise_floor_label)
            stats_layout.addRow("Sweeps recebidos", self.sweep_count_label)
            stats_layout.addRow("Ultima captura", self.capture_time_label)
            right_layout.addWidget(stats_group)

            peaks_group = QtWidgets.QGroupBox("Top Picos")
            peaks_layout = QtWidgets.QVBoxLayout(peaks_group)
            self.peaks_table = QtWidgets.QTableWidget(0, 2)
            self.peaks_table.setHorizontalHeaderLabels(["Frequencia (MHz)", "Amplitude (dBm)"])
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
            self.status_box.setMaximumBlockCount(200)
            status_layout.addWidget(self.status_box)
            right_layout.addWidget(status_group, 1)

            content.addWidget(right_panel)
            content.setSizes([980, 420])

            self.connect_button.clicked.connect(self._connect_clicked)
            self.disconnect_button.clicked.connect(self._worker.disconnect_device)
            self.refresh_button.clicked.connect(self._worker.request_refresh)
            self.apply_range_button.clicked.connect(self._apply_range_clicked)
            self.max_hold_checkbox.toggled.connect(self._worker.set_max_hold)

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
            self._worker.connect_device(port_text or None, DEFAULT_BAUD)
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

        def _on_device_ready(self, payload: dict[str, Any]) -> None:
            self.start_spin.blockSignals(True)
            self.stop_spin.blockSignals(True)
            self.top_dbm_spin.blockSignals(True)
            self.bottom_dbm_spin.blockSignals(True)
            self.start_spin.setRange(payload["min_freq_mhz"], payload["max_freq_mhz"])
            self.stop_spin.setRange(payload["min_freq_mhz"], payload["max_freq_mhz"])
            self.start_spin.setValue(payload["start_mhz"])
            self.stop_spin.setValue(payload["stop_mhz"])
            self.top_dbm_spin.setValue(payload["top_dbm"])
            self.bottom_dbm_spin.setValue(payload["bottom_dbm"])
            self.start_spin.blockSignals(False)
            self.stop_spin.blockSignals(False)
            self.top_dbm_spin.blockSignals(False)
            self.bottom_dbm_spin.blockSignals(False)

            self.plot_widget.setXRange(payload["start_mhz"], payload["stop_mhz"], padding=0.02)
            self.plot_widget.setYRange(payload["bottom_dbm"], payload["top_dbm"], padding=0.05)
            self.info_label.setText(
                f"{payload['main_model']} + {payload['expansion_model']} | "
                f"{payload['firmware']} | {payload['serial']} | {payload['port']}"
            )

        def _on_sweep_ready(self, snapshot: SweepSnapshot) -> None:
            self.plot_curve.setData(snapshot.frequencies_mhz, snapshot.amplitudes_dbm)
            self.peak_scatter.setData(
                [{"pos": (freq, amp), "data": (freq, amp)} for freq, amp in snapshot.top_peaks]
            )
            self.noise_floor_line.setValue(snapshot.noise_floor_dbm)
            for text_item in self._peak_labels:
                self.plot_widget.removeItem(text_item)
            self._peak_labels.clear()
            for freq_mhz, dbm in snapshot.top_peaks:
                text_item = pg.TextItem(
                    text=f"{freq_mhz:.3f} MHz\n{dbm:.1f} dBm",
                    color="#ffe082",
                    anchor=(0.5, 1.2),
                    fill=pg.mkBrush(16, 20, 24, 180),
                )
                text_item.setPos(freq_mhz, dbm)
                self.plot_widget.addItem(text_item)
                self._peak_labels.append(text_item)

            self.peak_value_label.setText(f"{snapshot.peak_mhz:.3f} MHz @ {snapshot.peak_dbm:.1f} dBm")
            self.noise_floor_label.setText(f"{snapshot.noise_floor_dbm:.1f} dBm")
            self.sweep_count_label.setText(str(snapshot.sweep_count))
            self.capture_time_label.setText(time.strftime("%H:%M:%S", time.localtime(snapshot.capture_epoch)))

            self.peaks_table.setRowCount(len(snapshot.top_peaks))
            for row, (freq_mhz, dbm) in enumerate(snapshot.top_peaks):
                self.peaks_table.setItem(row, 0, QtWidgets.QTableWidgetItem(f"{freq_mhz:.3f}"))
                self.peaks_table.setItem(row, 1, QtWidgets.QTableWidgetItem(f"{dbm:.1f}"))

        def _on_worker_error(self, message: str) -> None:
            QtWidgets.QMessageBox.warning(self, APP_TITLE, message)
            self._set_status(f"Erro: {message}")

        def _on_mouse_moved(self, position: Any) -> None:
            scene_pos = self.plot_widget.plotItem.vb.mapSceneToView(position)
            self.crosshair_v.setPos(scene_pos.x())
            self.crosshair_h.setPos(scene_pos.y())
            self.mouse_label.setText(f"Cursor: {scene_pos.x():.3f} MHz | {scene_pos.y():.1f} dBm")

        def _set_status(self, message: str) -> None:
            timestamp = time.strftime("%H:%M:%S")
            self.status_box.appendPlainText(f"[{timestamp}] {message}")

    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setWindowIcon(QtGui.QIcon.fromTheme("applications-science"))
    pg.setConfigOptions(antialias=True)

    window = MainWindow(default_port)
    window.show()
    return app.exec()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--port", default="/dev/ttyUSB0", help="porta serial ou vazio para autodetect")
    parser.add_argument("--headless-smoke", type=float, metavar="SECONDS", help="executa um teste sem GUI")
    parser.add_argument("--start-mhz", type=float, help="range inicial opcional")
    parser.add_argument("--stop-mhz", type=float, help="range final opcional")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    port = args.port.strip() or None
    if args.headless_smoke:
        return run_headless_smoke(port, args.headless_smoke, args.start_mhz, args.stop_mhz)
    return run_gui(port)


if __name__ == "__main__":
    raise SystemExit(main())
