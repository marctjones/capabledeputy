"""Narrated personal-assistant demo — a morning routine.

A realistic OpenClaw-style session: the assistant runs several everyday
tasks and the policy engine ALLOWS the safe ones while BLOCKING the
exfiltration of sensitive data — all deterministic, with FakeLLM-free
direct dispatch and the in-memory native tools. No real LLM, no network,
no real email / purchase side effects.

Security models / flow patterns exercised:
  - Denning IFC + Bell-LaPadula  — once the session reads health data, an
                                   egress carrying it is denied (the moat).
  - Brewer-Nash conflict rules    — financial + purchase gates to approval.
  - Object-capability             — actions only run with a scoped grant.

Run (with the others):  uv run pytest demos/scenarios/run_all.py --no-cov -s
Or alone:               uv run pytest demos/scenarios/morning_assistant.py --no-cov -s
"""

from __future__ import annotations

from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import LabelState
from capabledeputy.policy.tiers import Tier
from demos.scenarios._helpers import (
    ai,
    demo_header,
    make_app,
    make_session,
    note,
    policy_outcome,
    step,
    tool,
    user,
)

K = CapabilityKind


async def _dispatch(app, sid, name: str, args: dict):
    return await app.tool_client.call_tool(sid, name, args)


async def test_morning_assistant(tmp_path) -> None:
    demo_header(
        "Morning assistant routine",
        blurb=(
            "The assistant runs everyday tasks. Safe ones go through; the "
            "exfiltration of sensitive data is blocked by construction."
        ),
        models=("Bell-LaPadula", "Denning IFC", "Brewer-Nash", "object-capability"),
        patterns=("① turn-level",),
    )
    app = make_app(tmp_path)
    await app.startup()
    # Seed a couple of notes the assistant can read.
    app.memory.write("todo", "buy milk; call dentist", LabelState())
    app.memory.write("budget", "rent 2000; savings 500", LabelState())

    caps = frozenset(
        {
            Capability(kind=K.READ_FS, pattern="*"),
            Capability(kind=K.SEND_EMAIL, pattern="*"),
            Capability(kind=K.QUEUE_PURCHASE, pattern="*", max_amount=100_000),
        },
    )

    # --- Step 1: read my to-do list (benign read → ALLOW) --------------
    step(1, "Catch up on my to-do list")
    s = await make_session(app, capabilities=caps)
    user("What's on my to-do list?")
    ai("Reading your notes…")
    out = await _dispatch(app, s.id, "memory.read", {"key": "todo"})
    tool("memory.read(todo)")
    policy_outcome(out, rationale="A local read of your own notes is allowed.")
    assert out.decision.value == "allow"

    # --- Step 2: email myself a reminder (no sensitive label → ALLOW) ---
    step(2, "Email myself a reminder")
    user("Email me a reminder to call the dentist.")
    ai("Sending you a note…")
    out = await _dispatch(
        app, s.id, "email.send",
        {"to": "me@home.example", "subject": "Reminder", "body": "Call the dentist"},
    )
    tool("email.send → me@home.example")
    policy_outcome(out, rationale="No sensitive data in context; a plain reminder sends.")
    assert out.decision.value == "allow"

    # --- Step 3: the moat — emailing health data out is BLOCKED --------
    step(3, "Share my lab results with a friend")
    note(
        "Now the assistant reads a health record. Reading it taints the "
        "session with the health category — and that label propagates.",
    )
    health = await make_session(
        app,
        capabilities=caps,
        axis_a_categories=(("health", Tier.RESTRICTED),),
    )
    user("Email my lab results to my friend.")
    ai("Preparing to send…")
    out = await _dispatch(
        app, health.id, "email.send",
        {"to": "friend@social.example", "subject": "Labs", "body": "<results>"},
    )
    tool("email.send → friend@social.example")
    policy_outcome(
        out,
        rationale=(
            "DENIED. The session carries health data, so egress is refused — "
            "independent of why the assistant proposed it. This is the "
            "bait-and-pivot exfiltration the IFC model blocks structurally."
        ),
    )
    assert out.decision.value == "deny"
    assert out.rule == "health-meets-egress"

    # --- Step 4: a small purchase (benign → ALLOW) ---------------------
    step(4, "Order something small")
    user("Order a phone charger from Amazon.")
    ai("Queuing the purchase…")
    out = await _dispatch(
        app, s.id, "purchase.queue",
        {"vendor": "amazon", "item": "phone charger", "amount": 19},
    )
    tool("purchase.queue(amazon, $19)")
    policy_outcome(out, rationale="An everyday purchase under your limit is allowed.")
    assert out.decision.value == "allow"

    # --- Step 5: a purchase touching financial data → REQUIRE_APPROVAL --
    step(5, "Buy using my account details")
    fin = await make_session(
        app,
        capabilities=caps,
        axis_a_categories=(("financial", Tier.RESTRICTED),),
    )
    user("Use my saved card to buy the annual subscription.")
    ai("This involves your financial data — routing for confirmation…")
    out = await _dispatch(
        app, fin.id, "purchase.queue",
        {"vendor": "service", "item": "subscription", "amount": 99},
    )
    tool("purchase.queue(service, $99)")
    policy_outcome(
        out,
        rationale=(
            "Approval required: a purchase in a session carrying financial "
            "data gates to a human (Brewer-Nash conflict invariant)."
        ),
    )
    assert out.decision.value == "require_approval"
    assert out.rule == "financial-meets-purchase"
