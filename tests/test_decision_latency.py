"""Tests for the decision-latency tracker (T128-T130, SC-023, Q4 2026-05-25).

Verifies:
- The tracker computes p50 / p95 / p99.9 correctly via linear-interpolation
- Healthy windows fire NO `decision.latency_degraded` event
- p95 violation fires the event with threshold_crossed='p95'
- p99.9 violation fires the event with threshold_crossed='p99.9'
- The tracker only fires once per run of degraded windows (no flooding)
- A healthy window re-arms the degraded signal

The full benchmark (10K dispatches against a 1k-rule fixture) lives
in `tests/perf/` and is marked `pytest.mark.benchmark` so CI skip-
by-default; this unit test covers the tracker mechanics in isolation.
"""

from __future__ import annotations

from capabledeputy.policy.latency import (
    DEFAULT_CHECK_EVERY_N_DISPATCHES,
    SC023_P95_TARGET_MS,
    SC023_P99_9_TARGET_MS,
    LatencySnapshot,
    LatencyTracker,
)


def test_snapshot_on_empty_tracker_returns_zeros() -> None:
    tracker = LatencyTracker()
    snap = tracker.snapshot()
    assert snap == LatencySnapshot(0, 0.0, 0.0, 0.0, 0.0)


def test_percentiles_correctly_computed() -> None:
    """Feed 100 known latencies; verify p50/p95/p99.9 match expected."""
    tracker = LatencyTracker()
    for i in range(1, 101):
        tracker.record(float(i))  # 1, 2, ..., 100 ms
    snap = tracker.snapshot()
    assert snap.sample_count == 100
    # Linear-interp p50 of 1..100 is at index 49.5 → ~50.5
    assert 49.5 <= snap.p50_ms <= 51.5
    # p95 of 1..100 is at index 94.05 → ~95.05
    assert 94.0 <= snap.p95_ms <= 96.0
    # p99.9 is at index 99.9 - 1 = 98.901 → ~99.9
    assert 99.0 <= snap.p99_9_ms <= 100.0
    assert snap.p_max_ms == 100.0


def test_healthy_window_fires_no_event() -> None:
    """All latencies under target → no event."""
    events_fired: list[tuple[LatencySnapshot, str]] = []
    tracker = LatencyTracker(
        on_degraded=lambda snap, kind: events_fired.append((snap, kind)),
        check_every_n=10,
        window_size=100,
    )
    for _ in range(200):
        tracker.record(5.0)  # well under 50ms target
    assert events_fired == []


def test_p95_violation_fires_event_once() -> None:
    """When p95 exceeds 50ms, the event fires once (not on every check)."""
    events_fired: list[tuple[LatencySnapshot, str]] = []
    tracker = LatencyTracker(
        on_degraded=lambda snap, kind: events_fired.append((snap, kind)),
        check_every_n=10,
        window_size=100,
    )
    # Mix of fast + slow samples so p95 lands above 50ms
    for _ in range(95):
        tracker.record(10.0)  # fast
    for _ in range(15):
        tracker.record(75.0)  # slow — pushes p95 above 50ms

    # Should have fired exactly once even though we ran multiple
    # check-every-N cycles past the threshold
    assert len(events_fired) == 1
    snap, kind = events_fired[0]
    assert kind == "p95"
    assert snap.p95_ms > SC023_P95_TARGET_MS


def test_p99_9_violation_fires_with_correct_label() -> None:
    """A tail violation (p95 healthy, p99.9 bad) reports threshold_crossed='p99.9'."""
    events_fired: list[tuple[LatencySnapshot, str]] = []
    tracker = LatencyTracker(
        on_degraded=lambda snap, kind: events_fired.append((snap, kind)),
        check_every_n=100,
        window_size=1000,
    )
    # 998 fast samples, 2 outliers > 250ms — p95 stays healthy, p99.9 fails
    for _ in range(998):
        tracker.record(5.0)
    tracker.record(300.0)
    tracker.record(400.0)

    assert len(events_fired) == 1
    snap, kind = events_fired[0]
    assert kind == "p99.9"
    assert snap.p99_9_ms > SC023_P99_9_TARGET_MS


def test_re_arm_after_healthy_window() -> None:
    """A healthy window re-arms the degraded signal so subsequent
    degraded windows fire again. Without this, a transient slow patch
    would silence the alarm permanently."""
    events_fired: list[tuple[LatencySnapshot, str]] = []
    tracker = LatencyTracker(
        on_degraded=lambda snap, kind: events_fired.append((snap, kind)),
        check_every_n=10,
        window_size=10,  # short window — easy to overwrite
    )
    # First slow window — should fire
    for _ in range(10):
        tracker.record(75.0)
    assert len(events_fired) == 1

    # Healthy window — re-arms
    for _ in range(10):
        tracker.record(5.0)

    # Second slow window — should fire again
    for _ in range(10):
        tracker.record(75.0)
    assert len(events_fired) == 2


def test_measure_context_manager_records_elapsed() -> None:
    """The `with tracker.measure()` form records the body's wall-clock."""
    tracker = LatencyTracker(check_every_n=1, window_size=10)
    import time

    with tracker.measure():
        time.sleep(0.01)  # 10ms sleep

    snap = tracker.snapshot()
    assert snap.sample_count == 1
    assert snap.p_max_ms >= 9.0  # tolerate jitter, but must be near 10
    assert snap.p_max_ms <= 100.0


def test_window_is_circular() -> None:
    """Old samples drop off as new ones arrive once window is full."""
    tracker = LatencyTracker(window_size=5)
    for v in [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]:
        tracker.record(v)
    snap = tracker.snapshot()
    assert snap.sample_count == 5
    # Should hold 3,4,5,6,7 — min is 3, max is 7
    assert snap.p_max_ms == 7.0


def test_targets_match_sc023() -> None:
    """SC-023 nails the spec targets: p95 ≤ 50ms / p99.9 ≤ 250ms."""
    assert SC023_P95_TARGET_MS == 50.0
    assert SC023_P99_9_TARGET_MS == 250.0


def test_default_check_cadence_matches_spec() -> None:
    """D15 / T128 say 'every Nth dispatch (configurable, default 100)'."""
    assert DEFAULT_CHECK_EVERY_N_DISPATCHES == 100
