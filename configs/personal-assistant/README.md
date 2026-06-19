# Personal assistant — default policy bundle

A drop-in policy preset for using CapableDeputy as a daily-driver
personal AI assistant on your desktop through a terminal session.

The intent: the agent can do common personal-assistant work
(email triage, document drafting, calendar review, web research,
note-taking, code browsing) with minimal friction, while the
chokepoint still catches the dangerous combinations (sending email
about untrusted content you just fetched, exfiltrating personal
data to external services, irreversible destructive ops without
approval).

## What's in this bundle

| File | Purpose |
|---|---|
| `daemon.yaml` | Master config — wires up bundled local tools, Apple app automation, official Google Workspace MCP, and the policy files |
| `profiles.yaml` | One operator profile (`personal`) with max-tier ceiling |
| `purposes.yaml` | Five working purposes (general, inbox, calendar, writing, research) |
| `source_bindings.yaml` | Common macOS paths, Apple app URIs, and Google service URIs → label tier mappings |
| `envelopes.yaml` | Per-category risk-preference dial bounds |
| `override_policy.yaml` | Single-authorized operator can override hard floors; no dual-control required (single-user mode) |
| `approval-patterns.yaml` | Auto-approve common safe outbound patterns (self-email, calendar events for self) |

## Use

### Option A — copy to your XDG config

```bash
mkdir -p ~/.config/capabledeputy
cp configs/personal-assistant/*.yaml ~/.config/capabledeputy/
```

Edit paths in `source_bindings.yaml` to match your filesystem layout. The
defaults are macOS-first: `/Users/*/Documents`, `/Users/*/Desktop`,
`/Users/*/Documents/GitHub`, and `/Users/*/notes`.

### Option B — point the daemon at this directory

```bash
capdep daemon start --config configs/personal-assistant/daemon.yaml
```

Same effect; doesn't pollute your `~/.config/capabledeputy/`.

## What it gives the agent

- **Read** your local files under `~/Documents`, `~/Desktop`, `~/Documents/GitHub`, `~/notes`
- **Write** to `~/notes/scratch/**` without approval
- **Search the web** and read fetched content (labeled untrusted, naturally chokes egress)
- **Read Gmail / Drive / Calendar / Chat / People** after native `capdep oauth login` for the Workspace servers you enable
- **Read and draft in Apple Mail / Gmail**; direct send is disabled by default
- **Read/edit/export Pages and Numbers documents** with app-specific capability gates
- **Read/present Keynote decks** with app-specific capability gates
- **Use bounded macOS automation** for app listing/opening, clipboard read/write, and notifications
- **Create calendar events** with approval gate (one-prompt-per-event)
- **Persist memory** across sessions (via the bundled `mcp-server-memory`)
- **Browse local git repos** read-only

## What it refuses without override

- **Egress** (web fetch, email send) of `confidential.personal` data to non-trusted destinations
- **Reading** anything tagged `confidential.financial` while the session is also doing egress (Brewer-Nash)
- **Irreversible file ops** (delete, modify-in-place on protected paths) without approval
- **Direct email send** unless you deliberately enable a `SEND_EMAIL` server/config
- **Anything `prohibited` tier** without an explicit override (the override flow is single-authorized — that's you)

## Customizing

- **Paths look wrong?** Edit `source_bindings.yaml` — operator-curated, version-controlled.
- **Too many approvals?** Add patterns to `approval-patterns.yaml` for recurring workflows you trust (e.g., daily-briefing emails to yourself).
- **Want stricter handling for a category?** Add an entry to `envelopes.yaml` and the risk-preference dial will respect it.
- **Need a new working context?** Add a purpose to `purposes.yaml` — purposes carry their own bindings + default capabilities.

## Prerequisites

1. `capdep init` — onboarding (creates `~/.config/capabledeputy/`)
2. `export ANTHROPIC_API_KEY=sk-...` (or whichever LLM provider you chose)
3. (Optional) export `GOOGLE_MCP_CLIENT_ID` and `GOOGLE_MCP_CLIENT_SECRET`, then run:

   ```bash
   capdep oauth login --config configs/personal-assistant/daemon.yaml --server google-gmail
   capdep oauth login --config configs/personal-assistant/daemon.yaml --server google-drive
   capdep oauth login --config configs/personal-assistant/daemon.yaml --server google-calendar
   ```

4. (Optional) grant macOS Automation permissions when the OS prompts for Mail, Keynote, Pages, Numbers, or System Events.
5. (Optional) `export BRAVE_SEARCH_API_KEY=...` — better web search than DDG

Then:

```bash
capdep daemon start --config configs/personal-assistant/daemon.yaml
capdep session new --intent "morning briefing" --purpose general
capdep chat
```

## Where to inspect what the chokepoint did

- `capdep tool list` — every tool registered with its capability kind
- `capdep audit tail` — live audit stream
- `capdep tui` — graphical view of sessions / approvals / events
