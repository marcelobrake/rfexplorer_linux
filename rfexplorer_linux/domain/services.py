from __future__ import annotations

from typing import Sequence

import numpy as np

from rfexplorer_linux.config import (
    FREQUENCY_AXIS_TOLERANCE_MHZ,
    MAX_HISTORY_DEPTH,
    MAX_PEAKS,
    PEAK_MATCH_WINDOW_MHZ,
)
from rfexplorer_linux.domain.models import SweepSnapshot, TrackedPeak


def extract_top_peaks(
    frequencies: list[float], amplitudes: list[float], max_peaks: int = MAX_PEAKS
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


def build_sweep_snapshot(
    frequencies_mhz: list[float],
    amplitudes_dbm: list[float],
    capture_epoch: float,
    sweep_count: int,
) -> SweepSnapshot:
    top_peaks = extract_top_peaks(frequencies_mhz, amplitudes_dbm, MAX_PEAKS)
    noise_floor = estimate_noise_floor(amplitudes_dbm)
    peak_mhz, peak_dbm = top_peaks[0] if top_peaks else (0.0, -120.0)
    return SweepSnapshot(
        frequencies_mhz=frequencies_mhz,
        amplitudes_dbm=amplitudes_dbm,
        top_peaks=top_peaks,
        noise_floor_dbm=noise_floor,
        peak_dbm=peak_dbm,
        peak_mhz=peak_mhz,
        capture_epoch=capture_epoch,
        sweep_count=sweep_count,
    )


def prune_tracked_peaks(
    tracked_peaks: Sequence[TrackedPeak], now_epoch: float, timeout_sec: float
) -> list[TrackedPeak]:
    return [
        peak
        for peak in tracked_peaks
        if timeout_sec <= 0 or now_epoch - peak.last_seen_epoch <= timeout_sec
    ]


def update_tracked_peaks(
    tracked_peaks: Sequence[TrackedPeak],
    observed_peaks: Sequence[tuple[float, float]],
    now_epoch: float,
    timeout_sec: float,
    merge_window_mhz: float = PEAK_MATCH_WINDOW_MHZ,
) -> list[TrackedPeak]:
    active_peaks = prune_tracked_peaks(tracked_peaks, now_epoch, timeout_sec)
    consumed: set[int] = set()

    for freq_mhz, amp_dbm in observed_peaks:
        best_index: int | None = None
        best_distance: float | None = None
        for idx, peak in enumerate(active_peaks):
            if idx in consumed:
                continue
            distance = abs(peak.frequency_mhz - freq_mhz)
            if distance > merge_window_mhz:
                continue
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_index = idx

        if best_index is None:
            active_peaks.append(
                TrackedPeak(
                    frequency_mhz=freq_mhz,
                    amplitude_dbm=amp_dbm,
                    first_seen_epoch=now_epoch,
                    last_seen_epoch=now_epoch,
                )
            )
            consumed.add(len(active_peaks) - 1)
            continue

        matched_peak = active_peaks[best_index]
        matched_peak.frequency_mhz = freq_mhz
        matched_peak.amplitude_dbm = amp_dbm
        matched_peak.last_seen_epoch = now_epoch
        consumed.add(best_index)

    active_peaks = prune_tracked_peaks(active_peaks, now_epoch, timeout_sec)
    active_peaks.sort(key=lambda peak: (peak.amplitude_dbm, peak.last_seen_epoch), reverse=True)
    return active_peaks[:MAX_PEAKS]


def have_compatible_frequency_axis(
    reference: SweepSnapshot,
    candidate: SweepSnapshot,
    tolerance_mhz: float = FREQUENCY_AXIS_TOLERANCE_MHZ,
) -> bool:
    if len(reference.frequencies_mhz) != len(candidate.frequencies_mhz):
        return False
    if not reference.frequencies_mhz or not candidate.frequencies_mhz:
        return True
    return (
        abs(reference.frequencies_mhz[0] - candidate.frequencies_mhz[0]) <= tolerance_mhz
        and abs(reference.frequencies_mhz[-1] - candidate.frequencies_mhz[-1]) <= tolerance_mhz
    )


def append_history_snapshot(
    history: Sequence[SweepSnapshot], snapshot: SweepSnapshot, depth: int
) -> list[SweepSnapshot]:
    normalized_depth = max(1, min(int(depth), MAX_HISTORY_DEPTH))
    next_history = list(history)
    if next_history and not have_compatible_frequency_axis(next_history[-1], snapshot):
        next_history = []
    next_history.append(snapshot)
    return next_history[-normalized_depth:]


def build_history_surface_data(
    history: Sequence[SweepSnapshot],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if not history:
        return np.empty((0,)), np.empty((0,)), np.empty((0, 0))

    frequencies = np.asarray(history[-1].frequencies_mhz, dtype=float)
    amplitudes = np.asarray([snapshot.amplitudes_dbm for snapshot in history], dtype=float)
    sweep_indexes = np.arange(len(history), dtype=float)
    return frequencies, sweep_indexes, amplitudes

