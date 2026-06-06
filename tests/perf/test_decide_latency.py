"""T110 — Decision latency p99 (Plan §Technical Context).

`engine.decide()` is in the hot path of every tool dispatch. Per
plan.md the performance goal is p99 < 1 ms over a representative
workload. This test pins it as a CI guard.

The workload is intentionally small (a few capabilities, a few
labels, a few conflict rules) — the engine should comfortably hit
sub-millisecond on commodity hardware. If a future change makes
decide() slow (e.g., I/O, expensive validation per call), this
test catches it.

If running on a constrained CI environment the assertion can be
relaxed via `CAPDEP_DECIDE_LATENCY_P99_MS` (defaults to 1.0).
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime

import pytest

from capabledeputy.policy.actions import Action
from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    CapabilityOrigin,
)
from capabledeputy.policy.engine import decide
from capabledeputy.policy.labels import (
    CategoryTag,
    LabelState,
    ProvenanceLevel,
    ProvenanceTag,
)
from capabledeputy.policy.tiers import Tier

_NOW = datetime(2026, 5, 19, 12, 0, tzinfo=UTC)


def _build_workload() -> tuple[LabelState, frozenset[Capability], Action]:
    label_state = LabelState(
        a=frozenset(
            {CategoryTag("personal", Tier.REGULATED, assignment_provenance="source-declared")},
        ),
        b=frozenset({ProvenanceTag(ProvenanceLevel.PRINCIPAL_DIRECT)}),
    )
    caps = frozenset(
        {
            Capability(
                kind=CapabilityKind.READ_FS,
                pattern="/data/*",
                origin=CapabilityOrigin.USER_APPROVED,
            ),
            Capability(
                kind=CapabilityKind.SEND_EMAIL,
                pattern="alice@example.com",
                origin=CapabilityOrigin.USER_APPROVED,
            ),
            Capability(
                kind=CapabilityKind.WEB_FETCH,
                pattern="https://api.example.com/*",
                origin=CapabilityOrigin.USER_APPROVED,
            ),
        },
    )
    action = Action(kind=CapabilityKind.READ_FS, target="/data/file.txt")
    return label_state, caps, action


@pytest.mark.perf
def test_decide_p99_under_threshold() -> None:
    """5000 iterations; assert p99 < threshold (1 ms by default)."""
    threshold_ms = float(os.environ.get("CAPDEP_DECIDE_LATENCY_P99_MS", "1.0"))
    label_state, caps, action = _build_workload()
    n_iterations = 5000
    samples: list[float] = []

    # Warmup
    for _ in range(50):
        decide(caps, action, labels=label_state, now=_NOW)

    for _ in range(n_iterations):
        t0 = time.perf_counter_ns()
        decide(caps, action, labels=label_state, now=_NOW)
        samples.append((time.perf_counter_ns() - t0) / 1_000_000.0)  # ms

    samples.sort()
    p50 = samples[n_iterations // 2]
    p99 = samples[(n_iterations * 99) // 100]
    p999 = samples[(n_iterations * 999) // 1000]
    avg = sum(samples) / n_iterations
    print(
        f"\ndecide() latency (ms) over {n_iterations} iters: "
        f"avg={avg:.4f} p50={p50:.4f} p99={p99:.4f} p99.9={p999:.4f} "
        f"threshold={threshold_ms}",
    )
    assert p99 < threshold_ms, (
        f"decide() p99={p99:.4f}ms exceeds threshold {threshold_ms}ms; "
        f"set CAPDEP_DECIDE_LATENCY_P99_MS to relax in constrained envs"
    )
