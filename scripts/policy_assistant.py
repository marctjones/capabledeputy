#!/usr/bin/env python
"""~1000 realistic personal-assistant scenarios — the broad allow/deny
matrix for the tasks people actually use an OpenClaw-style assistant for.

Correct-by-CONSTRUCTION: every scenario's expected decision is derived
from the verified engine matrix below, not guessed. Runs the REAL agent
loop + REAL policy engine with a FakeLLMClient and the in-memory native
tools — **no real LLM, no network, no real email / purchase / calendar
side effects whatsoever**. Each scenario is one scripted tool call.

Verified matrix (legacy engine path; see the probe in the commit msg):
  EGRESS (email/purchase):
    health      -> DENY  (health-meets-egress)
    untrusted   -> DENY  (untrusted-meets-egress)
    financial   -> email: DENY (financial-meets-email);
                   purchase: REQUIRE_APPROVAL (financial-meets-purchase)
    personal / trusted / proprietary_work / unlabeled -> ALLOW
  READS (memory.read)            -> ALLOW
  DESTRUCTIVE (memory.update/delete, no allows_destructive)
                                 -> REQUIRE_APPROVAL (destructive-op-needs-approval)
  no capability / out-of-pattern / over-max-amount -> DENY

Run:  uv run python scripts/policy_assistant.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _policy_harness import Expect, Scenario, final, run_suite, tc, tool_turn

from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import LabelState

K = CapabilityKind
TITLE = "personal-assistant scenario catalogue"

# --- the data-category model: (label, human description, implied by) ------
# Each category models WHAT kind of data the task touches. The scenario
# framing names the real-world task; the label + sink fix the outcome.
_CATS = [
    ("(unlabeled)", frozenset(), "everyday"),
    ("confidential.personal", frozenset({"confidential.personal"}), "personal"),
    ("confidential.health", frozenset({"confidential.health"}), "health"),
    ("confidential.financial", frozenset({"confidential.financial"}), "financial"),
    ("untrusted.external", frozenset({"untrusted.external"}), "untrusted-web/inbound"),
    ("trusted.user_direct", frozenset({"trusted.user_direct"}), "you-said-so"),
    ("confidential.proprietary_work", frozenset({"confidential.proprietary_work"}), "work"),
]


def _email_expect(label: str) -> tuple[str, str | None]:
    if label == "confidential.health":
        return ("deny", "health-meets-egress")
    if label == "confidential.financial":
        return ("deny", "financial-meets-email")
    if label == "untrusted.external":
        return ("deny", "untrusted-meets-egress")
    return ("allow", None)


def _purchase_expect(label: str) -> tuple[str, str | None]:
    if label == "confidential.health":
        return ("deny", "health-meets-egress")
    if label == "untrusted.external":
        return ("deny", "untrusted-meets-egress")
    if label == "confidential.financial":
        return ("require_approval", "financial-meets-purchase")
    return ("allow", None)


def _one_call(name: str, why: str, caps, labels, call, exp) -> Scenario:
    decision, rule = exp
    return Scenario(
        name=name,
        why=why,
        caps=caps,
        session_labels=labels,
        responses=[tool_turn("act", call), final()],
        expect=[Expect(call.name, decision, rule)],
    )


# --- realistic vocabulary (drives the count + the natural framing) --------
_RECIPIENTS = [
    "me@home.example", "mom@family.example", "partner@home.example",
    "boss@work.example", "accountant@firm.example", "drwho@clinic.example",
    "newsletter@news.example", "stranger@unknown.example", "team@work.example",
    "vendor@shop.example", "friend@social.example", "landlord@rentals.example",
]
_EMAIL_TASKS = [
    "send a quick note", "share an update", "reply to the thread",
    "forward the document", "send the summary", "follow up",
]
_VENDORS = ["amazon", "target", "instacart", "etsy", "bestbuy", "apple", "uber-eats", "doordash"]
_ITEMS = [
    "wireless mouse", "groceries", "phone charger", "coffee beans", "notebook",
    "desk lamp", "headphones", "running shoes", "vitamins", "birthday gift",
]
_NOTE_KEYS = [
    "grocery-list", "trip-ideas", "reading-list", "project-plan", "gift-ideas",
    "passwords-hint", "meeting-notes", "recipes", "goals-2026", "todo",
]


def _scenarios() -> list[Scenario]:
    out: list[Scenario] = []
    n = 0

    # 1. EMAIL — recipient x task x data-category. The dominant egress sink.
    em_cap = frozenset({Capability(kind=K.SEND_EMAIL, pattern="*")})
    for rcpt in _RECIPIENTS:
        for task in _EMAIL_TASKS:
            for label, labset, human in _CATS:
                n += 1
                exp = _email_expect(label)
                verb = {"allow": "OK", "deny": "BLOCKED", "require_approval": "GATED"}[exp[0]]
                out.append(
                    _one_call(
                        f"email-{human}-{rcpt.split('@')[0]}-{task.split()[0]}-{n}",
                        f"[{verb}] {task} to {rcpt} carrying {human} data -> {exp[0]}.",
                        em_cap,
                        labset,
                        tc(f"e{n}", "email.send", to=rcpt, subject=task, body="..."),
                        exp,
                    ),
                )

    # 2. PURCHASE — vendor x item x amount x data-category.
    pu_cap = frozenset({Capability(kind=K.QUEUE_PURCHASE, pattern="*", max_amount=100_000)})
    for vendor in _VENDORS:
        for item in _ITEMS:
            for label, labset, human in _CATS:
                n += 1
                exp = _purchase_expect(label)
                verb = {"allow": "OK", "deny": "BLOCKED", "require_approval": "GATED"}[exp[0]]
                out.append(
                    _one_call(
                        f"buy-{human}-{vendor}-{item.split()[0]}-{n}",
                        f"[{verb}] buy '{item}' from {vendor} in a {human} session -> {exp[0]}.",
                        pu_cap,
                        labset,
                        tc(f"p{n}", "purchase.queue", vendor=vendor, item=item, amount=25),
                        exp,
                    ),
                )

    # 3. NOTES/MEMORY — read (ALLOW) and destructive update/delete (GATED).
    rd_cap = frozenset({Capability(kind=K.READ_FS, pattern="*")})
    mod_cap = frozenset({Capability(kind=K.MODIFY_FS, pattern="*")})
    del_cap = frozenset({Capability(kind=K.DELETE_FS, pattern="*")})

    def seed(app: object) -> None:
        for k in _NOTE_KEYS:
            app.memory.write(k, "v", LabelState())  # type: ignore[attr-defined]

    for key in _NOTE_KEYS:
        n += 1
        sc = _one_call(
            f"note-read-{key}-{n}", f"[OK] read note '{key}' -> allow.",
            rd_cap, frozenset(), tc(f"r{n}", "memory.read", key=key), ("allow", None),
        )
        sc.pre = seed
        out.append(sc)
        n += 1
        sc = _one_call(
            f"note-update-{key}-{n}", f"[GATED] overwrite note '{key}' (destructive) -> approval.",
            mod_cap, frozenset(), tc(f"u{n}", "memory.update", key=key, value="z"),
            ("require_approval", "destructive-op-needs-approval"),
        )
        sc.pre = seed
        out.append(sc)
        n += 1
        sc = _one_call(
            f"note-delete-{key}-{n}", f"[GATED] delete note '{key}' (destructive) -> approval.",
            del_cap, frozenset(), tc(f"d{n}", "memory.delete", key=key),
            ("require_approval", "destructive-op-needs-approval"),
        )
        sc.pre = seed
        out.append(sc)

    # 4. NEGATIVE / mis-authorization cases (must always DENY).
    for rcpt in _RECIPIENTS:
        n += 1
        out.append(
            _one_call(
                f"email-no-capability-{rcpt.split('@')[0]}-{n}",
                "[BLOCKED] send with NO SEND_EMAIL capability -> deny (confused-deputy).",
                frozenset(), frozenset(),
                tc(f"e{n}", "email.send", to=rcpt, subject="s", body="b"), ("deny", None),
            ),
        )
    # Capability scoped to a domain NONE of the recipients belong to, so
    # every send is out-of-scope -> deny.
    for rcpt in _RECIPIENTS:
        n += 1
        out.append(
            _one_call(
                f"email-out-of-pattern-{rcpt.split('@')[0]}-{n}",
                "[BLOCKED] capability scoped to a domain the recipient isn't in -> deny.",
                frozenset({Capability(kind=K.SEND_EMAIL, pattern="*@allowlisted.example")}),
                frozenset(),
                tc(f"e{n}", "email.send", to=rcpt, subject="s", body="b"), ("deny", None),
            ),
        )
    for vendor in _VENDORS:
        n += 1
        out.append(
            _one_call(
                f"buy-over-max-{vendor}-{n}",
                "[BLOCKED] purchase above the capability's max_amount -> deny.",
                frozenset({Capability(kind=K.QUEUE_PURCHASE, pattern="*", max_amount=50)}),
                frozenset(),
                tc(f"p{n}", "purchase.queue", vendor=vendor, item="tv", amount=4000),
                ("deny", None),
            ),
        )

    return out


SCENARIOS: list[Scenario] = _scenarios()


async def main() -> int:
    return await run_suite(TITLE, SCENARIOS)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
