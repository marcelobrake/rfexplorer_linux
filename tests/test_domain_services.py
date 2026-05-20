from __future__ import annotations

import unittest

from rfexplorer_linux.domain.models import SweepSnapshot, TrackedPeak
from rfexplorer_linux.domain.services import (
    append_history_snapshot,
    build_history_surface_data,
    build_sweep_snapshot,
    estimate_noise_floor,
    extract_top_peaks,
    prune_tracked_peaks,
    update_tracked_peaks,
)


def make_snapshot(
    frequencies: list[float],
    amplitudes: list[float],
    capture_epoch: float = 100.0,
    sweep_count: int = 1,
) -> SweepSnapshot:
    return build_sweep_snapshot(
        frequencies_mhz=frequencies,
        amplitudes_dbm=amplitudes,
        capture_epoch=capture_epoch,
        sweep_count=sweep_count,
    )


class DomainServicesTests(unittest.TestCase):
    def test_extract_top_peaks_handles_short_series(self) -> None:
        peaks = extract_top_peaks([100.0, 101.0], [-90.0, -80.0], 5)

        self.assertEqual(peaks, [(100.0, -90.0), (101.0, -80.0)])

    def test_extract_top_peaks_prefers_separated_local_maxima(self) -> None:
        frequencies = [430.0, 430.2, 430.4, 430.6, 430.8, 431.0, 431.2]
        amplitudes = [-100.0, -82.0, -96.0, -85.0, -97.0, -79.0, -98.0]

        peaks = extract_top_peaks(frequencies, amplitudes, 3)

        self.assertEqual(peaks[0], (431.0, -79.0))
        self.assertEqual(peaks[1], (430.2, -82.0))
        self.assertEqual(peaks[2], (430.6, -85.0))

    def test_estimate_noise_floor_uses_lower_portion_of_spectrum(self) -> None:
        self.assertEqual(estimate_noise_floor([-110.0, -101.0, -90.0, -80.0, -70.0]), -110.0)

    def test_estimate_noise_floor_handles_empty_values(self) -> None:
        self.assertEqual(estimate_noise_floor([]), -120.0)

    def test_build_sweep_snapshot_calculates_peak_and_noise_floor(self) -> None:
        snapshot = make_snapshot([100.0, 101.0, 102.0], [-110.0, -72.0, -99.0], capture_epoch=5.0, sweep_count=8)

        self.assertEqual(snapshot.peak_mhz, 101.0)
        self.assertEqual(snapshot.peak_dbm, -72.0)
        self.assertEqual(snapshot.capture_epoch, 5.0)
        self.assertEqual(snapshot.sweep_count, 8)

    def test_update_tracked_peaks_merges_close_frequency_and_refreshes_timestamp(self) -> None:
        tracked = [
            TrackedPeak(433.920, -82.0, first_seen_epoch=10.0, last_seen_epoch=10.0),
        ]

        updated = update_tracked_peaks(
            tracked_peaks=tracked,
            observed_peaks=[(433.990, -77.5)],
            now_epoch=11.0,
            timeout_sec=2.5,
        )

        self.assertEqual(len(updated), 1)
        self.assertAlmostEqual(updated[0].frequency_mhz, 433.990)
        self.assertAlmostEqual(updated[0].amplitude_dbm, -77.5)
        self.assertEqual(updated[0].first_seen_epoch, 10.0)
        self.assertEqual(updated[0].last_seen_epoch, 11.0)

    def test_update_tracked_peaks_adds_new_peak_when_outside_merge_window(self) -> None:
        tracked = [TrackedPeak(433.920, -82.0, first_seen_epoch=10.0, last_seen_epoch=10.0)]

        updated = update_tracked_peaks(
            tracked_peaks=tracked,
            observed_peaks=[(435.500, -78.0)],
            now_epoch=11.0,
            timeout_sec=2.5,
        )

        self.assertEqual(len(updated), 2)

    def test_update_tracked_peaks_keeps_entries_when_timeout_is_zero(self) -> None:
        tracked = [TrackedPeak(433.920, -82.0, first_seen_epoch=10.0, last_seen_epoch=1.0)]

        updated = update_tracked_peaks(
            tracked_peaks=tracked,
            observed_peaks=[],
            now_epoch=11.0,
            timeout_sec=0.0,
        )

        self.assertEqual(len(updated), 1)

    def test_prune_tracked_peaks_removes_expired_entries(self) -> None:
        tracked = [
            TrackedPeak(433.92, -81.0, first_seen_epoch=1.0, last_seen_epoch=8.0),
            TrackedPeak(434.55, -83.0, first_seen_epoch=1.0, last_seen_epoch=11.2),
        ]

        active = prune_tracked_peaks(tracked, now_epoch=12.0, timeout_sec=1.0)

        self.assertEqual(len(active), 1)
        self.assertAlmostEqual(active[0].frequency_mhz, 434.55)

    def test_extract_top_peaks_falls_back_to_global_peak_when_no_local_maxima(self) -> None:
        peaks = extract_top_peaks([100.0, 101.0, 102.0, 103.0], [-100.0, -95.0, -90.0, -85.0], 3)

        self.assertEqual(peaks, [(103.0, -85.0)])

    def test_append_history_snapshot_resets_when_frequency_axis_changes(self) -> None:
        history = [
            make_snapshot([430.0, 431.0, 432.0], [-100.0, -82.0, -95.0], capture_epoch=1.0, sweep_count=1),
            make_snapshot([430.0, 431.0, 432.0], [-101.0, -81.0, -96.0], capture_epoch=2.0, sweep_count=2),
        ]
        incompatible = make_snapshot(
            [860.0, 861.0, 862.0],
            [-110.0, -90.0, -99.0],
            capture_epoch=3.0,
            sweep_count=3,
        )

        next_history = append_history_snapshot(history, incompatible, depth=10)

        self.assertEqual(len(next_history), 1)
        self.assertEqual(next_history[0].frequencies_mhz[0], 860.0)

    def test_append_history_snapshot_respects_depth_floor(self) -> None:
        history = []
        for count in range(4):
            history = append_history_snapshot(
                history,
                make_snapshot([430.0, 431.0], [-100.0, -80.0 + count], capture_epoch=float(count), sweep_count=count),
                depth=2,
            )

        self.assertEqual(len(history), 2)

    def test_build_history_surface_data_returns_expected_shape(self) -> None:
        history = [
            make_snapshot([430.0, 431.0, 432.0], [-101.0, -80.0, -96.0], capture_epoch=1.0, sweep_count=1),
            make_snapshot([430.0, 431.0, 432.0], [-103.0, -78.0, -97.0], capture_epoch=2.0, sweep_count=2),
            make_snapshot([430.0, 431.0, 432.0], [-104.0, -79.0, -98.0], capture_epoch=3.0, sweep_count=3),
        ]

        frequencies, sweep_indexes, amplitude_grid = build_history_surface_data(history)

        self.assertEqual(frequencies.tolist(), [430.0, 431.0, 432.0])
        self.assertEqual(sweep_indexes.tolist(), [0.0, 1.0, 2.0])
        self.assertEqual(amplitude_grid.shape, (3, 3))

    def test_build_history_surface_data_handles_empty_history(self) -> None:
        frequencies, sweep_indexes, amplitude_grid = build_history_surface_data([])

        self.assertEqual(frequencies.size, 0)
        self.assertEqual(sweep_indexes.size, 0)
        self.assertEqual(amplitude_grid.size, 0)
