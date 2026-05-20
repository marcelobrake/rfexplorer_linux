from __future__ import annotations

import contextlib
import io
import os
import sys
import time
from pathlib import Path
from typing import Any

import serial

from rfexplorer_linux.config import DEFAULT_BAUD
from rfexplorer_linux.domain.models import AnalyzerConfiguration, SweepSnapshot
from rfexplorer_linux.domain.services import build_sweep_snapshot

PROJECT_DIR = Path(__file__).resolve().parents[2]


def resolve_rfexplorer_lib(
    environment: dict[str, str] | None = None,
    cwd: Path | None = None,
    project_dir: Path = PROJECT_DIR,
) -> Path | None:
    env = environment or os.environ
    current_dir = cwd or Path.cwd()
    candidates = [
        current_dir / "vendor" / "RFExplorer-for-Python",
        project_dir / "vendor" / "RFExplorer-for-Python",
    ]

    env_value = env.get("RFEXPLORER_LIB", "")
    if env_value:
        candidates.insert(0, Path(env_value).expanduser())

    for candidate in candidates:
        if (candidate / "RFExplorer" / "RFExplorer.py").exists():
            return candidate

    return None


def load_rfexplorer_module(
    environment: dict[str, str] | None = None,
    cwd: Path | None = None,
    project_dir: Path = PROJECT_DIR,
) -> tuple[Any | None, RuntimeError | None]:
    library_path = resolve_rfexplorer_lib(environment=environment, cwd=cwd, project_dir=project_dir)
    if library_path is None:
        return (
            None,
            RuntimeError(
                "Biblioteca RFExplorer-for-Python nao encontrada. "
                "Defina RFEXPLORER_LIB ou clone a biblioteca em vendor/RFExplorer-for-Python."
            ),
        )

    if library_path is not None and str(library_path) not in sys.path:
        sys.path.insert(0, str(library_path))

    try:
        import RFExplorer  # type: ignore

        return RFExplorer, None
    except ModuleNotFoundError:
        return None, RuntimeError("Falha ao importar RFExplorer mesmo com a biblioteca resolvida.")


def ensure_serial_access(port: str | None, baud_rate: int) -> None:
    if not port:
        return
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


def build_analyzer_configuration(rfe: Any) -> AnalyzerConfiguration:
    return AnalyzerConfiguration(
        port=rfe.PortName,
        firmware=rfe.RFExplorerFirmwareDetected,
        serial=rfe.SerialNumber,
        main_model=getattr(rfe.MainBoardModel, "name", str(rfe.MainBoardModel)),
        expansion_model=getattr(rfe.ExpansionBoardModel, "name", str(rfe.ExpansionBoardModel)),
        start_mhz=float(rfe.StartFrequencyMHZ),
        stop_mhz=float(rfe.StopFrequencyMHZ),
        top_dbm=float(rfe.AmplitudeTopDBM),
        bottom_dbm=float(rfe.AmplitudeBottomDBM),
        min_freq_mhz=float(rfe.MinFreqMHZ),
        max_freq_mhz=float(rfe.MaxFreqMHZ),
        min_span_mhz=float(rfe.MinSpanMHZ),
        max_span_mhz=float(rfe.MaxSpanMHZ),
    )


def snapshot_from_rfe_sweep(sweep: Any, sweep_count: int, capture_epoch: float | None = None) -> SweepSnapshot:
    total_points = sweep.TotalDataPoints
    frequencies = [sweep.GetFrequencyMHZ(index) for index in range(total_points)]
    amplitudes = [sweep.GetAmplitude_DBM(index) for index in range(total_points)]
    return build_sweep_snapshot(
        frequencies_mhz=frequencies,
        amplitudes_dbm=amplitudes,
        capture_epoch=capture_epoch or time.time(),
        sweep_count=sweep_count,
    )


class RFExplorerGateway:
    def __init__(self, baud_rate: int = DEFAULT_BAUD) -> None:
        self._baud_rate = baud_rate
        self._module, self._import_error = load_rfexplorer_module()
        self._rfe = None
        self._last_sweep_count = 0

    @property
    def is_connected(self) -> bool:
        return bool(self._rfe is not None and self._rfe.PortConnected)

    def _ensure_module(self) -> Any:
        if self._import_error is not None:
            raise self._import_error
        return self._module

    def connect(self, port: str | None) -> AnalyzerConfiguration:
        ensure_serial_access(port, self._baud_rate)
        rfexplorer_module = self._ensure_module()

        rfe = rfexplorer_module.RFECommunicator()
        rfe.VerboseLevel = 0
        rfe.UseByteBLOB = False
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rfe.GetConnectedPorts()
        if not rfe.ConnectPort(port, self._baud_rate):
            rfe.Close()
            raise RuntimeError("Nao foi possivel conectar ao RF Explorer.")

        deadline = time.time() + 8.0
        while time.time() < deadline and rfe.ActiveModel == rfexplorer_module.RFE_Common.eModel.MODEL_NONE:
            rfe.ProcessReceivedString(True)
            time.sleep(0.05)

        if rfe.ActiveModel == rfexplorer_module.RFE_Common.eModel.MODEL_NONE:
            rfe.Close()
            raise RuntimeError("Conectou na serial, mas nao recebeu a identificacao do analyzer.")

        rfe.CleanSweepData()
        self._rfe = rfe
        self._last_sweep_count = 0
        return build_analyzer_configuration(rfe)

    def disconnect(self) -> None:
        if self._rfe is None:
            return
        try:
            self._rfe.Close()
        finally:
            self._rfe = None
            self._last_sweep_count = 0

    def refresh_configuration(self) -> AnalyzerConfiguration:
        if self._rfe is None:
            raise RuntimeError("RF Explorer nao conectado.")
        self._rfe.SendCommand_RequestConfigData()
        deadline = time.time() + 2.0
        while time.time() < deadline:
            self._rfe.ProcessReceivedString(True)
            time.sleep(0.03)
        return build_analyzer_configuration(self._rfe)

    def apply_range(self, start_mhz: float, stop_mhz: float, top_dbm: float, bottom_dbm: float) -> AnalyzerConfiguration:
        if self._rfe is None:
            raise RuntimeError("RF Explorer nao conectado.")
        self._rfe.UpdateDeviceConfig(start_mhz, stop_mhz, top_dbm, bottom_dbm)
        time.sleep(0.8)
        self._rfe.CleanSweepData()
        self._last_sweep_count = 0
        return self.refresh_configuration()

    def set_max_hold(self, enabled: bool) -> None:
        if self._rfe is None:
            raise RuntimeError("RF Explorer nao conectado.")
        self._rfe.UseMaxHold = enabled

    def read_next_snapshot(self) -> SweepSnapshot | None:
        if self._rfe is None or not self._rfe.PortConnected:
            return None
        self._rfe.ProcessReceivedString(True)
        sweep_count = self._rfe.SweepData.Count
        if sweep_count <= 0 or sweep_count == self._last_sweep_count:
            return None
        sweep = self._rfe.SweepData.GetData(sweep_count - 1)
        if sweep is None:
            return None
        self._last_sweep_count = sweep_count
        return snapshot_from_rfe_sweep(sweep, sweep_count)
