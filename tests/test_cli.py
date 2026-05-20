from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from rfexplorer_linux.cli import build_arg_parser, run_headless_smoke
from rfexplorer_linux.domain.models import AnalyzerConfiguration
from rfexplorer_linux.domain.services import build_sweep_snapshot


class FakeGateway:
    def __init__(self) -> None:
        self.connected_port = None
        self.configuration = AnalyzerConfiguration(
            port="/dev/ttyUSB0",
            firmware="01.35",
            serial="SERIAL",
            main_model="MODEL_WSUB1G",
            expansion_model="MODEL_2400",
            start_mhz=430.0,
            stop_mhz=440.0,
            top_dbm=0.0,
            bottom_dbm=-120.0,
            min_freq_mhz=15.0,
            max_freq_mhz=2700.0,
            min_span_mhz=0.112,
            max_span_mhz=600.0,
        )
        self.snapshots = [
            build_sweep_snapshot([430.0, 431.0, 432.0], [-100.0, -79.0, -96.0], 100.0, 1),
            None,
        ]
        self.disconnected = False

    def connect(self, port: str | None) -> AnalyzerConfiguration:
        self.connected_port = port
        return self.configuration

    def apply_range(self, start_mhz: float, stop_mhz: float, top_dbm: float, bottom_dbm: float) -> AnalyzerConfiguration:
        self.configuration.start_mhz = start_mhz
        self.configuration.stop_mhz = stop_mhz
        return self.configuration

    def read_next_snapshot(self):
        return self.snapshots.pop(0) if self.snapshots else None

    def disconnect(self) -> None:
        self.disconnected = True


class CliTests(unittest.TestCase):
    def test_build_arg_parser_parses_expected_flags(self) -> None:
        args = build_arg_parser().parse_args(["--port", "/dev/ttyUSB1", "--headless-smoke", "2", "--start-mhz", "433.9"])

        self.assertEqual(args.port, "/dev/ttyUSB1")
        self.assertEqual(args.headless_smoke, 2.0)
        self.assertEqual(args.start_mhz, 433.9)

    @patch("rfexplorer_linux.cli.time.sleep", return_value=None)
    @patch("rfexplorer_linux.cli.time.time", side_effect=[100.0, 100.05, 100.10, 100.30])
    @patch("rfexplorer_linux.cli.RFExplorerGateway")
    def test_run_headless_smoke_connects_prints_and_disconnects(self, gateway_cls, *_mocks) -> None:
        fake_gateway = FakeGateway()
        gateway_cls.return_value = fake_gateway
        buffer = io.StringIO()

        with redirect_stdout(buffer):
            exit_code = run_headless_smoke("/dev/ttyUSB0", duration_sec=0.2, start_mhz=None, stop_mhz=None)

        output = buffer.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertEqual(fake_gateway.connected_port, "/dev/ttyUSB0")
        self.assertTrue(fake_gateway.disconnected)
        self.assertIn("Conectado:", output)
        self.assertIn("peak=431.000 MHz", output)

    @patch("rfexplorer_linux.cli.time.sleep", return_value=None)
    @patch("rfexplorer_linux.cli.time.time", side_effect=[100.0, 100.3])
    @patch("rfexplorer_linux.cli.RFExplorerGateway")
    def test_run_headless_smoke_applies_range_when_requested(self, gateway_cls, *_mocks) -> None:
        fake_gateway = FakeGateway()
        gateway_cls.return_value = fake_gateway

        with redirect_stdout(io.StringIO()):
            run_headless_smoke("/dev/ttyUSB0", duration_sec=0.1, start_mhz=433.9, stop_mhz=434.4)

        self.assertEqual(fake_gateway.configuration.start_mhz, 433.9)
        self.assertEqual(fake_gateway.configuration.stop_mhz, 434.4)

    @patch("rfexplorer_linux.cli.run_gui", return_value=77)
    @patch("rfexplorer_linux.cli.build_arg_parser")
    def test_main_routes_to_gui_when_no_headless_smoke(self, parser_builder, run_gui) -> None:
        parser = parser_builder.return_value
        parser.parse_args.return_value = type(
            "Args",
            (),
            {"port": "/dev/ttyUSB0", "headless_smoke": None, "start_mhz": None, "stop_mhz": None},
        )()

        from rfexplorer_linux.cli import main

        exit_code = main()

        self.assertEqual(exit_code, 77)
        run_gui.assert_called_once_with("/dev/ttyUSB0")

    @patch("rfexplorer_linux.cli.run_headless_smoke", return_value=13)
    @patch("rfexplorer_linux.cli.build_arg_parser")
    def test_main_routes_to_headless_smoke(self, parser_builder, smoke_runner) -> None:
        parser = parser_builder.return_value
        parser.parse_args.return_value = type(
            "Args",
            (),
            {"port": " ", "headless_smoke": 2.0, "start_mhz": 430.0, "stop_mhz": 440.0},
        )()

        from rfexplorer_linux.cli import main

        exit_code = main()

        self.assertEqual(exit_code, 13)
        smoke_runner.assert_called_once_with(None, 2.0, 430.0, 440.0)
