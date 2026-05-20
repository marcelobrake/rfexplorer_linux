from __future__ import annotations

import unittest

from rfexplorer_linux.application.viewer_state import ViewerStateService
from rfexplorer_linux.domain.services import build_sweep_snapshot


def make_snapshot(
    frequencies: list[float],
    amplitudes: list[float],
    capture_epoch: float,
    sweep_count: int,
):
    return build_sweep_snapshot(
        frequencies_mhz=frequencies,
        amplitudes_dbm=amplitudes,
        capture_epoch=capture_epoch,
        sweep_count=sweep_count,
    )


class ViewerStateServiceTests(unittest.TestCase):
    def test_ingest_snapshot_updates_last_snapshot_history_and_peaks(self) -> None:
        service = ViewerStateService(peak_timeout_sec=3.0, history_depth=5)
        snapshot = make_snapshot([430.0, 431.0, 432.0], [-100.0, -82.0, -96.0], 10.0, 1)

        state = service.ingest_snapshot(snapshot)

        self.assertIs(state.last_snapshot, snapshot)
        self.assertEqual(len(state.history), 1)
        self.assertEqual(len(state.tracked_peaks), 1)
        self.assertAlmostEqual(state.tracked_peaks[0].frequency_mhz, 431.0)

    def test_expire_peaks_reports_change_when_entries_time_out(self) -> None:
        service = ViewerStateService(peak_timeout_sec=1.0, history_depth=5)
        service.ingest_snapshot(make_snapshot([430.0, 431.0, 432.0], [-100.0, -82.0, -96.0], 10.0, 1))

        state, changed = service.expire_peaks(now_epoch=12.0)

        self.assertTrue(changed)
        self.assertEqual(state.tracked_peaks, [])

    def test_set_peak_timeout_prunes_existing_peaks(self) -> None:
        service = ViewerStateService(peak_timeout_sec=5.0, history_depth=5)
        service.ingest_snapshot(make_snapshot([430.0, 431.0, 432.0], [-100.0, -82.0, -96.0], 10.0, 1))

        state = service.set_peak_timeout(0.5, now_epoch=11.0)

        self.assertEqual(state.tracked_peaks, [])
        self.assertEqual(service.peak_timeout_sec, 0.5)

    def test_set_history_depth_trims_existing_history(self) -> None:
        service = ViewerStateService(peak_timeout_sec=3.0, history_depth=5)
        for count in range(5):
            service.ingest_snapshot(
                make_snapshot([430.0, 431.0, 432.0], [-100.0, -82.0 + count, -96.0], 10.0 + count, count + 1)
            )

        state = service.set_history_depth(2)

        self.assertEqual(len(state.history), 2)
        self.assertEqual(service.history_depth, 2)

    def test_replace_history_replaces_existing_snapshots(self) -> None:
        service = ViewerStateService(peak_timeout_sec=3.0, history_depth=2)
        snapshots = [
            make_snapshot([430.0, 431.0, 432.0], [-100.0, -82.0, -96.0], 10.0, 1),
            make_snapshot([430.0, 431.0, 432.0], [-101.0, -81.0, -97.0], 11.0, 2),
            make_snapshot([430.0, 431.0, 432.0], [-102.0, -80.0, -98.0], 12.0, 3),
        ]

        state = service.replace_history(snapshots)

        self.assertEqual(len(state.history), 2)
        self.assertEqual(state.history[0].sweep_count, 2)

    def test_reset_clears_state(self) -> None:
        service = ViewerStateService()
        service.ingest_snapshot(make_snapshot([430.0, 431.0, 432.0], [-100.0, -82.0, -96.0], 10.0, 1))

        state = service.reset()

        self.assertIsNone(state.last_snapshot)
        self.assertEqual(state.history, [])
        self.assertEqual(state.tracked_peaks, [])
