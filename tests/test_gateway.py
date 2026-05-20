from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import serial

from rfexplorer_linux.domain.models import AnalyzerConfiguration
from rfexplorer_linux.infrastructure.rfexplorer_gateway import (
    RFExplorerGateway,
    build_analyzer_configuration,
    ensure_serial_access,
    load_rfexplorer_module,
    resolve_rfexplorer_lib,
    snapshot_from_rfe_sweep,
)


class FakeSweep:
    TotalDataPoints = 3

    def GetFrequencyMHZ(self, index: int) -> float:
        return [430.0, 431.0, 432.0][index]

    def GetAmplitude_DBM(self, index: int) -> float:
        return [-100.0, -81.0, -97.0][index]


class FakeRFE:
    PortName = "/dev/ttyUSB0"
    RFExplorerFirmwareDetected = "01.35"
    SerialNumber = "ABC123"
    MainBoardModel = type("Model", (), {"name": "MODEL_WSUB1G"})()
    ExpansionBoardModel = type("Model", (), {"name": "MODEL_2400"})()
    StartFrequencyMHZ = 430.0
    StopFrequencyMHZ = 440.0
    AmplitudeTopDBM = 0.0
    AmplitudeBottomDBM = -120.0
    MinFreqMHZ = 15.0
    MaxFreqMHZ = 2700.0
    MinSpanMHZ = 0.112
    MaxSpanMHZ = 600.0
    PortConnected = True

    class SweepData:
        Count = 1

        @staticmethod
        def GetData(index: int) -> FakeSweep:
            return FakeSweep()

    def ProcessReceivedString(self, read_blocking: bool) -> None:
        return None

    def Close(self) -> None:
        self.closed = True

    def SendCommand_RequestConfigData(self) -> None:
        self.requested_config = True

    def UpdateDeviceConfig(self, start_mhz: float, stop_mhz: float, top_dbm: float, bottom_dbm: float) -> None:
        self.updated_range = (start_mhz, stop_mhz, top_dbm, bottom_dbm)

    def CleanSweepData(self) -> None:
        self.cleaned = True


class ConnectableFakeRFE(FakeRFE):
    ActiveModel = "READY"

    def __init__(self) -> None:
        self.closed = False
        self.requested_config = False
        self.cleaned = False
        self.UseByteBLOB = True
        self.VerboseLevel = 99
        self.PortConnected = True
        self.UseMaxHold = False

    def GetConnectedPorts(self) -> None:
        self.scanned_ports = True

    def ConnectPort(self, port: str | None, baud_rate: int) -> bool:
        self.connected_args = (port, baud_rate)
        return True


class FakeRFExplorerModule:
    class RFE_Common:
        class eModel:
            MODEL_NONE = "NONE"

    def __init__(self, communicator: ConnectableFakeRFE) -> None:
        self._communicator = communicator

    def RFECommunicator(self) -> ConnectableFakeRFE:
        return self._communicator


class GatewayTests(unittest.TestCase):
    def test_resolve_rfexplorer_lib_prefers_env_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_root = Path(temp_dir) / "env-lib"
            env_root.joinpath("RFExplorer").mkdir(parents=True)
            env_root.joinpath("RFExplorer", "RFExplorer.py").write_text("# stub\n", encoding="utf-8")

            resolved = resolve_rfexplorer_lib(environment={"RFEXPLORER_LIB": str(env_root)}, cwd=Path(temp_dir))

            self.assertEqual(resolved, env_root)

    def test_load_rfexplorer_module_returns_error_when_library_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            module, error = load_rfexplorer_module(environment={}, cwd=Path(temp_dir), project_dir=Path(temp_dir))

            self.assertIsNone(module)
            self.assertIsInstance(error, RuntimeError)

    @patch("rfexplorer_linux.infrastructure.rfexplorer_gateway.serial.Serial")
    def test_ensure_serial_access_returns_when_port_is_none(self, serial_ctor) -> None:
        ensure_serial_access(None, 500000)
        serial_ctor.assert_not_called()

    @patch("rfexplorer_linux.infrastructure.rfexplorer_gateway.serial.Serial")
    def test_ensure_serial_access_re_raises_non_permission_errors(self, serial_ctor) -> None:
        serial_ctor.side_effect = serial.SerialException("other error")

        with self.assertRaises(serial.SerialException):
            ensure_serial_access("/dev/ttyUSB0", 500000)

    def test_ensure_serial_access_translates_permission_denied(self) -> None:
        with patch("rfexplorer_linux.infrastructure.rfexplorer_gateway.serial.Serial") as serial_ctor:
            serial_ctor.side_effect = serial.SerialException("[Errno 13] Permission denied")

            with self.assertRaises(RuntimeError) as ctx:
                ensure_serial_access("/dev/ttyUSB0", 500000)

        self.assertIn("Sem permissao", str(ctx.exception))

    def test_build_analyzer_configuration_maps_fields(self) -> None:
        configuration = build_analyzer_configuration(FakeRFE())

        self.assertIsInstance(configuration, AnalyzerConfiguration)
        self.assertEqual(configuration.port, "/dev/ttyUSB0")
        self.assertEqual(configuration.main_model, "MODEL_WSUB1G")
        self.assertEqual(configuration.expansion_model, "MODEL_2400")

    def test_snapshot_from_rfe_sweep_builds_domain_snapshot(self) -> None:
        snapshot = snapshot_from_rfe_sweep(FakeSweep(), sweep_count=7, capture_epoch=123.0)

        self.assertEqual(snapshot.sweep_count, 7)
        self.assertEqual(snapshot.capture_epoch, 123.0)
        self.assertEqual(snapshot.peak_mhz, 431.0)

    def test_gateway_read_next_snapshot_returns_none_when_not_connected(self) -> None:
        gateway = RFExplorerGateway()

        self.assertIsNone(gateway.read_next_snapshot())

    def test_gateway_read_next_snapshot_uses_last_count_guard(self) -> None:
        gateway = RFExplorerGateway()
        fake_rfe = FakeRFE()
        gateway._rfe = fake_rfe
        gateway._last_sweep_count = 0

        first = gateway.read_next_snapshot()
        second = gateway.read_next_snapshot()

        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_gateway_is_connected_uses_underlying_port_flag(self) -> None:
        gateway = RFExplorerGateway()
        gateway._rfe = FakeRFE()

        self.assertTrue(gateway.is_connected)

    def test_gateway_ensure_module_raises_import_error(self) -> None:
        gateway = RFExplorerGateway()
        gateway._import_error = RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            gateway._ensure_module()

    @patch("rfexplorer_linux.infrastructure.rfexplorer_gateway.ensure_serial_access")
    @patch("rfexplorer_linux.infrastructure.rfexplorer_gateway.load_rfexplorer_module")
    def test_gateway_connect_disconnect_refresh_apply_range_and_max_hold(
        self,
        load_module,
        ensure_access,
    ) -> None:
        communicator = ConnectableFakeRFE()
        module = FakeRFExplorerModule(communicator)
        load_module.return_value = (module, None)
        gateway = RFExplorerGateway()

        configuration = gateway.connect("/dev/ttyUSB0")
        refreshed = gateway.refresh_configuration()
        applied = gateway.apply_range(433.0, 435.0, 0.0, -110.0)
        gateway.set_max_hold(True)
        gateway.disconnect()

        ensure_access.assert_called()
        self.assertEqual(configuration.port, "/dev/ttyUSB0")
        self.assertEqual(refreshed.port, "/dev/ttyUSB0")
        self.assertEqual(applied.port, "/dev/ttyUSB0")
        self.assertEqual(communicator.connected_args, ("/dev/ttyUSB0", gateway._baud_rate))
        self.assertEqual(communicator.updated_range, (433.0, 435.0, 0.0, -110.0))
        self.assertTrue(communicator.requested_config)
        self.assertTrue(communicator.cleaned)
        self.assertTrue(communicator.UseMaxHold)
        self.assertTrue(communicator.closed)

