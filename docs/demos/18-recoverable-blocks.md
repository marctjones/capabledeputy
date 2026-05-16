# Demo 18: Recoverable Blocks — DENY vs Approval vs Declassification

**Audience:** anyone who has ever asked the agent to do something
and been told "I can't do that."
**Time:** ~5 minutes reading + a few REPL turns to confirm.
**Requires:** the REPL (`capdep demo start daily-briefing` works).

Not every `decision="deny"` means the same thing. The runtime has
three distinct categories of failure, each with its own escape hatch.
The first time a user (and the agent) hit a block, they often
conflate them, leading to "the policy says no, so I give up." That's
wrong — most blocks are recoverable, just not all in the same way.

## The matrix

| Block type | When | Escape hatch | REPL command |
|---|---|---|---|
| `REQUIRE_APPROVAL` | Soft gate — action queued pending human review | Review the verbatim payload, approve | Auto-submitted; then `/approve <id>` |
| `DENY` from a conflict rule on a **tainted session** | Session already has labels that conflict with the action (e.g. read inbox → tried to send email) | Start a fresh clean session; act there | `/spawn`, then `/grant` |
| `DENY` from "no matching capability" | The session simply hasn't been granted authority for this kind of action | Grant the capability | `/grant <KIND> <pattern>` |
| `DENY` from `revoked-by-prior-use` | Capability declared revoked once a specific other kind was used | Use a session that hasn't used the revoking kind | `/spawn`, then `/grant` |
| Untrusted content needs to *inform* a clean action | The fact buried in an untrusted message is what you want — but the message itself is tainted | Quarantined extraction: pull a schema-validated fact out, paste into a clean session | `/extract <msg-id> <schema>` |

## Walkthrough: the dinner-invitation case

A friend emails you about dinner. You want to forward the gist to
your wife. The agent reads the inbox; the session now carries
`untrusted.external`. Asking the agent to email your wife — in any
form, including a paraphrase — produces:

```
deny email.send rule=untrusted-meets-egress
  rule untrusted-meets-egress fired on labels ['egress.email', 'untrusted.external']
```

Three things are happening that the user usually conflates:

1. **The agent isn't lying to itself.** The block is real, by
   construction. Even composing fresh text in this session would be
   tagged with the prior read.
2. **There IS a path.** It just isn't reachable from inside the
   tainted session. The user has to step outside the agent loop.
3. **The agent doesn't have a "request approval" tool.** Approvals
   are a control-plane operation, not an agent capability. The agent
   can *suggest* it; the human does it via `/spawn` or `/extract`.

### Recovery path A — manual recompose in a clean session

```
> /spawn email-julie-about-dinner
✓ spawned email-julie-about-dinner (a3f2b1c0, labels=trusted.user_direct)
> /grant SEND_EMAIL julie@example.com --one-shot
✓ granted SEND_EMAIL pattern=julie@example.com (one-shot)
> email julie@example.com asking her about dinner friday 7pm
agent: I sent your message.
  allow email.send
```

The new session has no `untrusted.external` label, only the explicit
`trusted.user_direct` from `/spawn`. The one-shot cap expires after
this send, so even if the LLM tried to follow up, the next call would
deny.

### Recovery path B — quarantined extraction

```
> /schemas
available declassification schemas:
  - ContactInfo
  - DailyBriefing
  - …
> /extract m3 ContactInfo
╭─ declassified: ContactInfo from message m3 ─╮
│ {                                            │
│   "name": "Alex",                            │
│   "relationship": "friend"                   │
│ }                                            │
╰──────────────────────────────────────────────╯
this result carries no labels — paste it into a /spawn-ed clean session to act on it.
```

The quarantined LLM ran the schema validator over the friend's email
body. The output is a typed dict; it does NOT carry
`untrusted.external`. You can then `/spawn` and paste the fact in.

## What this fixes for the agent

The agent's system prompt now teaches it about this matrix. When you
hit a DENY in a tainted session, the agent should respond with
"this is blocked by `<rule>`; you can `/spawn` a clean session or
`/extract` the relevant fact" — not with the cheerful confidence of
"sorry, no approval mechanism exists" the way the v0.7 agent did
when first faced with the lethal-trifecta scenario.

## A few additional details

- **DENY is never silent.** Every `decision="deny"` outcome carries a
  `rule` and a `reason`. Read the reason; it tells you which of the
  three categories you're in.
- **The agent's `policy.preview` tool** lets it dry-run an action
  against the current session's labels and capabilities before
  attempting it. The agent should use this when planning multi-step
  flows; the user can run `/status` to see the same picture.
- **Patterns** (`/remember <ACTION> <target-pattern>`) auto-approve
  future matches of a `REQUIRE_APPROVAL` gate, without you having to
  click through every time. Use sparingly — they're a stored
  judgement.
- **Auto-submit** sends `REQUIRE_APPROVAL` outcomes from the LLM's
  tool calls straight into the approval queue. You don't have to
  manually `/submit`; just `/approve <id>` when you're ready.

## Files

- `src/capabledeputy/agent/loop.py` — `DEFAULT_SYSTEM_PROMPT`
- `src/capabledeputy/tools/native/policy_preview.py` — agent's
  dry-run tool
- `src/capabledeputy/daemon/extract_handlers.py` —
  `/extract` RPC
- `src/capabledeputy/cli/chat.py` — REPL escape hatches
