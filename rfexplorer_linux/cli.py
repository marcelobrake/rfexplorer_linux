from __future__ import annotations

import argparse
import time

from rfexplorer_linux.config import APP_TITLE
from rfexplorer_linux.infrastructure.rfexplorer_gateway import RFExplorerGateway
from rfexplorer_linux.presentation.qt_viewer import run_gui


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--port", default="/dev/ttyUSB0", help="porta serial ou vazio para autodetect")
    parser.add_argument("--headless-smoke", type=float, metavar="SECONDS", help="executa um teste sem GUI")
    parser.add_argument("--start-mhz", type=float, help="range inicial opcional")
    parser.add_argument("--stop-mhz", type=float, help="range final opcional")
    return parser


def run_headless_smoke(
    port: str | None,
    duration_sec: float,
    start_mhz: float | None,
    stop_mhz: float | None,
) -> int:
    gateway = RFExplorerGateway()
    try:
        configuration = gateway.connect(port)
        if start_mhz is not None and stop_mhz is not None:
            configuration = gateway.apply_range(start_mhz, stop_mhz, configuration.top_dbm, configuration.bottom_dbm)

        print(
            f"Conectado: {configuration.main_model} + {configuration.expansion_model} | "
            f"firmware {configuration.firmware} | serial {configuration.serial}"
        )

        end_time = time.time() + duration_sec
        while time.time() < end_time:
            snapshot = gateway.read_next_snapshot()
            if snapshot is not None:
                print(
                    f"sweep={snapshot.sweep_count} peak={snapshot.peak_mhz:.3f} MHz "
                    f"{snapshot.peak_dbm:.1f} dBm "
                    f"range={snapshot.frequencies_mhz[0]:.3f}-{snapshot.frequencies_mhz[-1]:.3f} MHz"
                )
            time.sleep(0.05)
        return 0
    finally:
        gateway.disconnect()


def main() -> int:
    args = build_arg_parser().parse_args()
    port = args.port.strip() or None
    if args.headless_smoke:
        return run_headless_smoke(port, args.headless_smoke, args.start_mhz, args.stop_mhz)
    return run_gui(port)
