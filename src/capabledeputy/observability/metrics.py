"""In-process metrics registry (#323).

`compliance/otlp.py` is a one-shot offline audit→JSON export; there were no live
counters / latency histograms / error rates. This is a tiny, dependency-free
registry the runtime updates as it works: counters (turns, errors, tool calls)
and latency histograms (turn / LLM / tool durations) with p50/p95/p99. A
`snapshot()` renders it for `capdep metrics`, a status RPC, or an OTLP export —
without adding a Prometheus/OTLP client dependency to the hot path.

Thread-safe (a lock guards updates); deterministic percentiles from the stored
samples (bounded per series so memory can't grow unbounded).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

_MAX_SAMPLES = 4096  # per histogram series — bound the memory


def _percentile(sorted_samples: list[float], q: float) -> float:
    if not sorted_samples:
        return 0.0
    if len(sorted_samples) == 1:
        return sorted_samples[0]
    rank = q * (len(sorted_samples) - 1)
    lo = int(rank)
    frac = rank - lo
    if lo + 1 >= len(sorted_samples):
        return sorted_samples[lo]
    return sorted_samples[lo] * (1 - frac) + sorted_samples[lo + 1] * frac


@dataclass(frozen=True)
class HistogramSummary:
    count: int
    p50: float
    p95: float
    p99: float
    max: float


@dataclass(frozen=True)
class MetricsSnapshot:
    counters: dict[str, int]
    histograms: dict[str, HistogramSummary]

    def as_dict(self) -> dict:
        return {
            "counters": dict(self.counters),
            "histograms": {
                k: {"count": h.count, "p50": h.p50, "p95": h.p95, "p99": h.p99, "max": h.max}
                for k, h in self.histograms.items()
            },
        }


@dataclass
class Metrics:
    _counters: dict[str, int] = field(default_factory=dict)
    _histograms: dict[str, list[float]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def incr(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + amount

    def observe(self, name: str, value: float) -> None:
        with self._lock:
            series = self._histograms.setdefault(name, [])
            series.append(value)
            if len(series) > _MAX_SAMPLES:
                del series[0 : len(series) - _MAX_SAMPLES]

    @contextmanager
    def timer(self, name: str, *, error_counter: str | None = None) -> Iterator[None]:
        """Time a block into histogram `name` (seconds); on exception, increment
        `error_counter` (if given) and re-raise."""
        start = time.perf_counter()
        try:
            yield
        except BaseException:
            if error_counter:
                self.incr(error_counter)
            raise
        finally:
            self.observe(name, time.perf_counter() - start)

    def snapshot(self) -> MetricsSnapshot:
        with self._lock:
            counters = dict(self._counters)
            histograms: dict[str, HistogramSummary] = {}
            for name, samples in self._histograms.items():
                s = sorted(samples)
                histograms[name] = HistogramSummary(
                    count=len(s),
                    p50=_percentile(s, 0.50),
                    p95=_percentile(s, 0.95),
                    p99=_percentile(s, 0.99),
                    max=s[-1] if s else 0.0,
                )
        return MetricsSnapshot(counters=counters, histograms=histograms)


_METRICS = Metrics()


def get_metrics() -> Metrics:
    """The process-wide metrics registry."""
    return _METRICS


def reset_metrics() -> None:
    """Reset the global registry (tests)."""
    global _METRICS
    _METRICS = Metrics()
