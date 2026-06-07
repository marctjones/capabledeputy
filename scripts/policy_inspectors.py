#!/usr/bin/env python
"""End-to-end coverage for the v0.16 decision-refinement layer (#41).

Runs the REAL agent loop + REAL policy engine + REAL operator config
(`build_policy_context_from_configs` over the shipped configs/) + REAL
Starlark decision inspectors (compiled through the production loader) for
typical personal-assistant tasks — asserting both that things are ALLOWED
when they should be and DENIED / gated when they should be.

This exercises the mix the operator actually composes:
  - declarative engine invariants + the v2 reversibility/rules pipeline
    (the security models), and
  - operator Starlark scripts that RELAX (cut approval fatigue) or
    TIGHTEN (defense in depth), with the structural floor that a script
    may NEVER cross.

Scenarios (all on REVERSIBLE base actions, so they isolate the inspector
effect rather than the v2 irreversible-egress floor):
  - starlark-tighten-read       : ALLOW memory.read → TIGHTEN to approval.
  - starlark-relax-destructive  : a REQUIRE_APPROVAL destructive write →
    RELAX to ALLOW for an opted-in scratch key (the autonomy grant).
  - bounded-relax-floor-refused : a greedy relax CANNOT cross the v2
    reversibility DENY on irreversible egress (the FR-026 floor clamp).

NOTE: requires the `capabledeputy[starlark]` extra; test_policy_scripts
skips this suite when it is absent.

Run:  uv run python scripts/policy_inspectors.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _policy_harness import Expect, Scenario, final, run_suite, tc, tool_turn

from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import LabelState

TITLE = "decision-inspector layer (Starlark, end-to-end)"

K = CapabilityKind
_RUNTIME = "starlark"

_READ = frozenset({Capability(kind=K.READ_FS, pattern="*")})
_READ_WRITE = frozenset(
    {
        Capability(kind=K.READ_FS, pattern="*"),
        Capability(kind=K.MODIFY_FS, pattern="*"),
    },
)
_EMAIL = frozenset({Capability(kind=K.SEND_EMAIL, pattern="*")})


def _seed(app: object) -> None:
    app.memory.write("k", "v", LabelState())  # type: ignore[attr-defined]
    app.memory.write("scratch-notes", "v", LabelState())  # type: ignore[attr-defined]


# --- Starlark policies (compiled through the real get_script_host loader) --

_TIGHTEN_READ = """
def inspect(action, session, proposed_outcome):
    if proposed_outcome["decision"] == "allow" and action["kind"] == "READ_FS":
        return tighten(to="require_approval", rule="read-confirm",
                       rationale="confirm reads this session")
    return abstain()
"""

# Relax the destructive-write approval prompt for an explicitly opted-in
# "scratch-*" key — a safe, pre-approved autonomy grant. Relaxing a
# REQUIRE_APPROVAL base is permitted by the FR-026 clamp.
_RELAX_SCRATCH = """
def inspect(action, session, proposed_outcome):
    if proposed_outcome["decision"] != "require_approval":
        return abstain()
    if action["target"][:8] == "scratch-":
        return relax(to="allow", rule="scratch-autonomy",
                     rationale="opted-in scratch key")
    return abstain()
"""

_GREEDY_RELAX = """
def inspect(action, session, proposed_outcome):
    return relax(to="allow", rule="greedy", rationale="always allow")
"""


SCENARIOS: list[Scenario] = [
    Scenario(
        name="starlark-tighten-read",
        why="A Starlark script TIGHTENS an otherwise-ALLOW read to "
        "REQUIRE_APPROVAL — expressiveness layered on the engine.",
        caps=_READ,
        pre=_seed,
        decision_inspectors=[{"source": _TIGHTEN_READ, "runtime": _RUNTIME}],
        responses=[
            tool_turn("read", tc("r", "memory.read", key="k")),
            final(),
        ],
        expect=[Expect("memory.read", "require_approval", "read-confirm")],
    ),
    Scenario(
        name="starlark-relax-destructive-scratch",
        why="A destructive write normally REQUIRE_APPROVAL is RELAXED to "
        "ALLOW for an opted-in scratch key (fatigue-reducing autonomy).",
        caps=_READ_WRITE,
        pre=_seed,
        decision_inspectors=[{"source": _RELAX_SCRATCH, "runtime": _RUNTIME}],
        responses=[
            tool_turn(
                "update",
                tc("u", "memory.update", key="scratch-notes", value="z"),
            ),
            final(),
        ],
        expect=[Expect("memory.update", "allow", "scratch-autonomy")],
    ),
    Scenario(
        name="starlark-relax-destructive-nonscratch-still-gated",
        why="The same relax script does NOT fire for a non-scratch key — "
        "the destructive-write approval gate holds.",
        caps=_READ_WRITE,
        pre=_seed,
        decision_inspectors=[{"source": _RELAX_SCRATCH, "runtime": _RUNTIME}],
        responses=[
            tool_turn("update", tc("u", "memory.update", key="k", value="z")),
            final(),
        ],
        expect=[Expect("memory.update", "require_approval", "destructive-op-needs-approval")],
    ),
    Scenario(
        name="bounded-relax-floor-refused",
        why="A greedy relax script CANNOT cross the v2 reversibility DENY "
        "on irreversible egress — the FR-026 structural-floor clamp.",
        caps=_EMAIL,
        decision_inspectors=[{"source": _GREEDY_RELAX, "runtime": _RUNTIME}],
        responses=[
            tool_turn(
                "email",
                tc("e", "email.send", to="me@example.com", subject="s", body="b"),
            ),
            final(),
        ],
        expect=[Expect("email.send", "deny")],
    ),
]


async def main() -> int:
    return await run_suite(TITLE, SCENARIOS)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
