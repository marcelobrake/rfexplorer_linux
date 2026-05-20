from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from rfexplorer_linux.config import DEFAULT_HISTORY_DEPTH, DEFAULT_PEAK_TIMEOUT_SEC
from rfexplorer_linux.domain.models import SweepSnapshot, TrackedPeak
from rfexplorer_linux.domain.services import append_history_snapshot, prune_tracked_peaks, update_tracked_peaks


@dataclass
class ViewerState:
    history: list[SweepSnapshot] = field(default_factory=list)
    tracked_peaks: list[TrackedPeak] = field(default_factory=list)
    last_snapshot: SweepSnapshot | None = None


class ViewerStateService:
    def __init__(
        self,
        peak_timeout_sec: float = DEFAULT_PEAK_TIMEOUT_SEC,
        history_depth: int = DEFAULT_HISTORY_DEPTH,
    ) -> None:
        self._peak_timeout_sec = peak_timeout_sec
        self._history_depth = history_depth
        self._state = ViewerState()

    @property
    def state(self) -> ViewerState:
        return self._state

    @property
    def peak_timeout_sec(self) -> float:
        return self._peak_timeout_sec

    @property
    def history_depth(self) -> int:
        return self._history_depth

    def reset(self) -> ViewerState:
        self._state = ViewerState()
        return self._state

    def ingest_snapshot(self, snapshot: SweepSnapshot) -> ViewerState:
        self._state.last_snapshot = snapshot
        self._state.history = append_history_snapshot(self._state.history, snapshot, self._history_depth)
        self._state.tracked_peaks = update_tracked_peaks(
            tracked_peaks=self._state.tracked_peaks,
            observed_peaks=snapshot.top_peaks,
            now_epoch=snapshot.capture_epoch,
            timeout_sec=self._peak_timeout_sec,
        )
        return self._state

    def expire_peaks(self, now_epoch: float) -> tuple[ViewerState, bool]:
        next_peaks = prune_tracked_peaks(self._state.tracked_peaks, now_epoch, self._peak_timeout_sec)
        changed = len(next_peaks) != len(self._state.tracked_peaks)
        self._state.tracked_peaks = next_peaks
        return self._state, changed

    def set_peak_timeout(self, timeout_sec: float, now_epoch: float | None = None) -> ViewerState:
        self._peak_timeout_sec = timeout_sec
        if now_epoch is not None:
            self._state.tracked_peaks = prune_tracked_peaks(
                self._state.tracked_peaks,
                now_epoch,
                self._peak_timeout_sec,
            )
        return self._state

    def set_history_depth(self, depth: int) -> ViewerState:
        self._history_depth = depth
        self._state.history = self._state.history[-depth:]
        return self._state

    def replace_history(self, history: Sequence[SweepSnapshot]) -> ViewerState:
        self._state.history = list(history)[-self._history_depth :]
        return self._state

