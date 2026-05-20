from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TrackedPeak:
    frequency_mhz: float
    amplitude_dbm: float
    first_seen_epoch: float
    last_seen_epoch: float


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


@dataclass
class AnalyzerConfiguration:
    port: str
    firmware: str
    serial: str
    main_model: str
    expansion_model: str
    start_mhz: float
    stop_mhz: float
    top_dbm: float
    bottom_dbm: float
    min_freq_mhz: float
    max_freq_mhz: float
    min_span_mhz: float
    max_span_mhz: float

