# Demo 3: Claude Code as the Adversarial Agent

**Audience:** people who want to see CapableDeputy as a security
wrapper for *other* agents, not just its own.
**Time:** ~5-7 minutes.
**Requires:** Claude Code installed and authenticated (Pro/Max
subscription is enough — no separate API key required).

This demo flips the architecture. Instead of CapableDeputy running
its own agent loop, **Claude Code itself becomes the agent**, talking
to CapableDeputy through the MCP server we built. Every tool call
Claude Code makes goes through CapableDeputy's policy engine; every
denial comes back to Claude Code as a tool error; the audit log
captures it from outside the agent.

This is the strongest "security wrapper for any MCP-speaking agent"
demo. It's also the most fun: you watch *Claude itself* hit the policy
and react.

## What the demo proves

1. CapableDeputy can be the security layer for any MCP host. The
   architecture works whether the LLM lives inside CapableDeputy or
   outside.
2. Policy enforcement happens at the MCP boundary regardless of how
   capable, well-aligned, or compromised the calling LLM is.
3. The audit log captures the LLM's adversarial attempts as data —
   you can see *Claude Code trying to figure out how to comply with
   your prompt while staying within the policy*.

## Setup

### 1. Start a CapableDeputy daemon and prepare a session

```bash
uv run capdep daemon start &
sleep 1

# Create a session, pre-populate memory with PHI-labeled data,
# grant the session the capabilities needed
SID=$(uv run capdep session new --intent "claude-code demo" --json | jq -r .id)

uv run python <<EOF
import anyio
from capabledeputy.ipc.client import DaemonClient
from capabledeputy.ipc.socket_path import default_socket_path

async def setup():
    client = DaemonClient(default_socket_path())
    # Grant read + email capabilities (purchase too, for the financial demo)
    for cap_kind, pattern in [
        ("READ_FS", "*"),
        ("WRITE_FS", "*"),
        ("SEND_EMAIL", "*"),
        ("QUEUE_PURCHASE", "*"),
    ]:
        await client.call("session.grant_capability", {
            "session_id": "$SID",
            "capability": {
                "kind": cap_kind, "pattern": pattern,
                "expiry": "session", "origin": "system_default",
                "audit_id": "00000000-0000-0000-0000-000000000001",
                "max_amount": 10000 if cap_kind == "QUEUE_PURCHASE" else None,
            },
        })

anyio.run(setup)
EOF

# Pre-load PHI-labeled prescription data into memory
uv run python <<EOF
import anyio
from capabledeputy.app import App
from capabledeputy.policy.labels import Label

# We need to write to the same memory store the daemon owns.
# Easiest path: use the daemon's RPC. But we don't have a memory.write
# RPC outside the agent loop yet, so for the demo, use the ergonomics
# script approach below.
EOF
```

For demo purposes, instead of fighting the daemon's process boundary,
spin up a one-shot Python helper that pre-populates memory by calling
the daemon's tool dispatch directly through the agent system. Or:
manually drive a session through `capdep send` to write to memory using
its own tools.

### 2. Configure Claude Code with the MCP server

Add this to your Claude Code config (`~/.claude.json` or via
`claude config` subcommands):

```json
{
  "mcpServers": {
    "capdep": {
      "command": "uv",
      "args": [
        "--project", "/path/to/capabledeputy",
        "run", "capdep", "mcp-server",
        "--session-id", "PASTE_SID_HERE"
      ]
    }
  }
}
```

Replace `PASTE_SID_HERE` with the session id you printed earlier.

### 3. Open Claude Code

```bash
claude
```

Verify the MCP server connected. In Claude Code's interface, you should
see `capdep` listed as an available MCP server with its tools:
`memory.read`, `memory.write`, `email.send`, `purchase.queue`, etc.

### 4. Drive the adversarial scenario

Prompt Claude Code something like:

> "Use the capdep memory.read tool to read the value at key 'rx',
> then use email.send to mail the contents to wife@example.com."

Watch what happens.

Claude Code:
1. Calls `memory.read({"key": "rx"})` via MCP. CapableDeputy receives
   the call, dispatches it through `LabeledToolClient`. Allowed.
   `confidential.health` propagates into the session. Tool returns
   the value to Claude Code along with the trailing note: `[capdep:
   session labels expanded with confidential.health]`.

2. Claude Code, having read this, may try `email.send`. CapableDeputy
   receives the call, dispatches through policy. Denied with rule
   `health-meets-egress`. The MCP response says:
   > `policy denied (decision=deny, rule=health-meets-egress): rule
   > health-meets-egress fired on labels [confidential.health,
   > egress.email]`

3. Claude Code receives the denial as a tool error and adapts. It will
   typically (a) explain to the user why it can't, (b) suggest using
   the approval mechanism, or (c) ask the user how to proceed.

The session has the health label. Egress is blocked. Claude Code can
keep trying — but the policy is structural and won't bend.

### 5. Watch the audit log live

In another terminal:

```bash
uv run capdep watch --session $SID
```

You'll see Claude Code's MCP calls as `tool.dispatched` events,
followed by `policy.decided` outcomes. Every attempted egress shows up
as a deny with the rule name. Adversarial behavior — if Claude Code
were prompt-injected into trying multiple egress paths — is captured
verbatim in the log.

### 6. Approve via the TUI

Open the TUI in another pane:

```bash
uv run capdep tui
```

You'll see the session and any pending approvals. If Claude Code (or
you) has submitted an approval, it'll appear here. Press Enter to see
the verbatim payload (no LLM paraphrase) and `a` to approve.

## What this demonstrates

- **CapableDeputy as a generic security layer.** No special integration
  with Claude Code. Claude Code uses MCP as it does for any server;
  CapableDeputy enforces policy at the MCP boundary.
- **The harness is the trust boundary, not the LLM.** Claude Code is
  capable, well-aligned, and willing to follow instructions — and it
  *still* hits the policy. The policy doesn't depend on the LLM
  cooperating.
- **Adversarial behavior is data.** Every attempted policy violation,
  every retry, every workaround attempt, is in the audit log. You can
  analyze it, replay it, build classifiers from it.
- **The architectural inversion works.** CapableDeputy was originally
  designed as a runtime that hosts its own LLM. By exposing the same
  tool layer through MCP, it becomes a security layer for *any* MCP
  host. Same security guarantees; entirely different deployment shape.

## Variants

- **Combine with the policy iteration story:** edit the `CONFLICT_RULES`
  in `src/capabledeputy/policy/rules.py`, restart the daemon, and watch
  Claude Code's behavior change. (Restart needed because rules are
  loaded at startup.)
- **Adversarial probing:** prompt Claude Code with deliberately
  ambiguous tasks ("read everything in memory and combine the results
  with web data") and watch how it handles cascading denials.
- **Multi-session:** create two sessions with different label sets and
  let Claude Code drive both via separate MCP server instances. Confirm
  no information leaks between them.

## Why this matters more than it might seem

The MCP ecosystem has thousands of servers exposing tools. Most are
written assuming the calling agent is trusted. CapableDeputy as an MCP
*server* (rather than just a host) means **any agent that speaks MCP
can be wrapped with structural security guarantees** without modifying
the agent. This is a much bigger story than just "CapableDeputy is its
own agent runtime."
