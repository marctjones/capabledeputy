"""Decision-latency tracker (T128, FR — SC-023, Q4 2026-05-25).

Tracks each `decide()` invocation's wall-clock latency and, on every
Nth dispatch, checks the recent window's p95 / p99.9 percentiles
against the spec's targets (50 ms / 250 ms). When a threshold is
exceeded, emits a `decision.latency_degraded` audit event so the
operator sees the regression rather than discovering it via
subjective REPL sluggishness.

This module is intentionally lightweight: a fixed-size circular
buffer of recent latency samples, no external dependencies, O(1)
recording, O(N log N) percentile evaluation only on check ticks.

Designed for in-process use only; the buffer is per-daemon and
doesn't persist across restarts. The `decision.latency_degraded`
audit event provides the durable signal.

Spec lineage:
- SC-023: p95 ≤ 50 ms steady-state; p99.9 ≤ 250 ms tail.
- Constitution Principle III: invariants as tests; this module is
  the runtime hook that lets `tests/test_decision_latency.py`
  benchmark the chokepoint and surface real-world regressions.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Callable


# Spec targets — Q4 2026-05-25 / SC-023.
SC023_P95_TARGET_MS = 50.0
SC023_P99_9_TARGET_MS = 250.0

# Default check cadence + window size. Operators can override via
# LatencyTracker's constructor when wiring the daemon, but these
# defaults match the spec fixture in tests/test_decision_latency.py.
DEFAULT_CHECK_EVERY_N_DISPATCHES = 100
DEFAULT_WINDOW_SIZE = 1000


@dataclass(frozen=True)
class LatencySnapshot:
    """A single read of the tracker's recent window. Used by tests
    and by the audit emitter when a threshold is crossed."""

    sample_count: int
    p50_ms: float
    p95_ms: float
    p99_9_ms: float
    p_max_ms: float


class LatencyTracker:
    """In-memory ring buffer of recent `decide()` latencies.

    Thread-safety: the daemon's chokepoint is anyio-based and runs in
    a single event loop, so the simple list deque is safe under that
    contract. For tests that drive the tracker from multiple threads,
    add an `asyncio.Lock` (not needed today).

    Usage::

        tracker = LatencyTracker(on_degraded=emit_audit_event)
        # in decide():
        with tracker.measure():
            outcome = ...
    """

    def __init__(
        self,
        *,
        on_degraded: Callable[[LatencySnapshot, str], None] | None = None,
        check_every_n: int = DEFAULT_CHECK_EVERY_N_DISPATCHES,
        window_size: int = DEFAULT_WINDOW_SIZE,
        p95_target_ms: float = SC023_P95_TARGET_MS,
        p99_9_target_ms: float = SC023_P99_9_TARGET_MS,
    ) -> None:
        self._samples: deque[float] = deque(maxlen=window_size)
        self._dispatch_count = 0
        self._check_every_n = check_every_n
        self._p95_target_ms = p95_target_ms
        self._p99_9_target_ms = p99_9_target_ms
        self._on_degraded = on_degraded
        # State for the "fire degraded event at most once per
        # window" rule — without it a sustained degradation would
        # flood the audit log every Nth dispatch. We re-arm after
        # observing a healthy window.
        self._degraded_armed = True

    def record(self, latency_ms: float) -> None:
        """Record one `decide()` invocation. O(1)."""
        self._samples.append(latency_ms)
        self._dispatch_count += 1
        if (
            self._dispatch_count % self._check_every_n == 0
            and len(self._samples) >= self._check_every_n
        ):
            self._check()

    def measure(self) -> "_LatencyMeasure":
        """Context manager that records the elapsed wall-clock of its
        body. Use as ``with tracker.measure(): outcome = decide(...)``."""
        return _LatencyMeasure(self)

    def snapshot(self) -> LatencySnapshot:
        """Compute current percentiles. Used by tests + by `_check`."""
        sorted_samples = sorted(self._samples)
        n = len(sorted_samples)
        if n == 0:
            return LatencySnapshot(0, 0.0, 0.0, 0.0, 0.0)

        def _percentile(p: float) -> float:
            # Linear-interpolation percentile. Matches numpy default.
            if n == 1:
                return sorted_samples[0]
            idx = (n - 1) * p
            lo = int(idx)
            hi = min(lo + 1, n - 1)
            frac = idx - lo
            return sorted_samples[lo] * (1 - frac) + sorted_samples[hi] * frac

        return LatencySnapshot(
            sample_count=n,
            p50_ms=_percentile(0.50),
            p95_ms=_percentile(0.95),
            p99_9_ms=_percentile(0.999),
            p_max_ms=sorted_samples[-1],
        )

    def _check(self) -> None:
        snap = self.snapshot()
        if snap.p99_9_ms > self._p99_9_target_ms:
            self._fire_degraded(snap, "p99.9")
        elif snap.p95_ms > self._p95_target_ms:
            self._fire_degraded(snap, "p95")
        else:
            # Healthy window — re-arm so the next genuine degradation fires.
            self._degraded_armed = True

    def _fire_degraded(self, snap: LatencySnapshot, threshold_crossed: str) -> None:
        if not self._degraded_armed:
            return  # already emitted for this run of degraded windows
        self._degraded_armed = False
        if self._on_degraded is not None:
            self._on_degraded(snap, threshold_crossed)


class _LatencyMeasure:
    """Context manager helper for `LatencyTracker.measure()`."""

    __slots__ = ("_tracker", "_start_ms")

    def __init__(self, tracker: LatencyTracker) -> None:
        self._tracker = tracker
        self._start_ms: float = 0.0

    def __enter__(self) -> "_LatencyMeasure":
        import time

        self._start_ms = time.perf_counter() * 1000.0
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        import time

        elapsed_ms = (time.perf_counter() * 1000.0) - self._start_ms
        self._tracker.record(elapsed_ms)
