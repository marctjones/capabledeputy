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
| `relationship_groups.yaml` | Self/trusted/family/work recipient groups used to reduce prompts without broadening authority |
| `envelopes.yaml` | Per-category risk-preference dial bounds |
| `override_policy.yaml` | Single-authorized operator can override hard floors; no dual-control required (single-user mode) |
| `approval-patterns.yaml` | Auto-approve common safe draft patterns after you replace placeholder addresses |
| `../policies/*.star` | Starlark decision inspectors enabled by `daemon.yaml` for tightening-only local automation, egress, and frequency checks |

## Use

### Option A — copy to your XDG config

```bash
mkdir -p ~/.config/capabledeputy
cp configs/personal-assistant/*.yaml ~/.config/capabledeputy/
```

Edit paths in `source_bindings.yaml` and addresses in
`relationship_groups.yaml` / `approval-patterns.yaml` before relying on the
low-friction draft paths. The defaults are macOS-first:
`/Users/*/Documents`, `/Users/*/Desktop`, `/Users/*/Documents/GitHub`, and
`/Users/*/notes`.

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
- **Read and draft in Apple Mail / Gmail**; direct send is disabled by default.
  Self/trusted-recipient drafts can stay low-friction once relationship groups
  are customized.
- **Read/edit/export Pages and Numbers documents** with app-specific capability gates
- **Read/present Keynote decks** with app-specific capability gates
- **Use bounded macOS automation** for app listing/opening, clipboard read/write, and notifications
- **Run Starlark policy inspectors** that prompt on first active local app use, high-tier draft/egress materialization, and repeated automation loops
- **Create calendar events** with purpose/relationship-aware confirmation.
  Self-calendar mutations in the `calendar` purpose can avoid first-use prompts
  when no high-tier data is present; external attendee changes remain gated.
- **Persist memory** across sessions (via the bundled `mcp-server-memory`)
- **Browse local git repos** read-only

## What it refuses without override

- **Egress** (web fetch, email send) of `confidential.personal` data to non-trusted destinations
- **Reading** anything tagged `confidential.financial` while the session is also doing egress (Brewer-Nash)
- **Irreversible file ops** (delete, modify-in-place on protected paths) without approval
- **Direct email send** unless you deliberately enable a `SEND_EMAIL` server/config
- **Silent first-use AppleScript writes/clipboard access**; the Starlark layer requires approval before the first active local automation in a session
- **Anything `prohibited` tier** without an explicit override (the override flow is single-authorized — that's you)

## Customizing

- **Paths look wrong?** Edit `source_bindings.yaml` — operator-curated, version-controlled.
- **Too many approvals?** First replace placeholders in
  `relationship_groups.yaml`, then add exact-address patterns to
  `approval-patterns.yaml` for recurring draft workflows you trust. Prefer
  drafts and self-calendar workflows; keep direct sends approval-gated.
- **Want stricter handling for a category?** Add an entry to `envelopes.yaml` and the risk-preference dial will respect it.
- **Want more/less desktop friction?** Edit `../policies/local_app_confirm.star` or remove it from `daemon.yaml`. Keep relax scripts disabled unless you have workflow-specific tests.
- **Need a new working context?** Add a purpose to `purposes.yaml` — purposes carry their own bindings + default capabilities.

## Prerequisites

1. `capdep init` — onboarding (creates `~/.config/capabledeputy/`)
2. Install the Starlark policy runtime if your environment did not install dev extras:

   ```bash
   pip install 'capabledeputy[starlark]'
   ```

3. `export ANTHROPIC_API_KEY=sk-...` (or whichever LLM provider you chose)
4. (Optional) export `GOOGLE_MCP_CLIENT_ID` and `GOOGLE_MCP_CLIENT_SECRET`, then run:

   ```bash
   capdep oauth login --config configs/personal-assistant/daemon.yaml --server google-gmail
   capdep oauth login --config configs/personal-assistant/daemon.yaml --server google-drive
   capdep oauth login --config configs/personal-assistant/daemon.yaml --server google-calendar
   ```

5. (Optional) grant macOS Automation permissions when the OS prompts for Mail, Keynote, Pages, Numbers, or System Events.
6. (Optional) web search providers (export before `capdep daemon start`):
   - `export KAGI_API_KEY=...` — full web/news search via `kagi_search_fetch`
     (wired in `daemon.yaml`; also `servers.d/kagi.yaml` example)
   - `export BRAVE_SEARCH_API_KEY=...` — full search via bundled `search.web`
   - With neither key, bundled search uses DuckDuckGo Instant Answer only
     (factoid queries; not headlines)

## Making AppleScript/macOS usable without broad ambient authority

- Start with the bundled app-specific servers (`mcp-server-apple-mail`,
  `mcp-server-keynote`, `mcp-server-pages`, `mcp-server-numbers`,
  `mcp-server-macos`) rather than the generic AppleScript catalog server.
- Grant macOS Automation/TCC permissions only to the terminal app running
  `capdep`, and only for the apps you intend to automate.
- Keep `MACOS_AUTOMATION` out of new grants. Use granular kinds such as
  `APPLE_MAIL_DRAFT`, `PAGES_EDIT`, `NUMBERS_EDIT`, `KEYNOTE_PRESENT`, and
  `MACOS_CLIPBOARD_READ`.
- Prefer read-only tools for normal context gathering. The preset prompts on
  first clipboard read/write, document edit/export, app control, and
  presentation/external calendar mutation. Notifications and trusted/self draft
  preparation avoid first-use prompts to reduce approval fatigue.
- Bind app surfaces explicitly in `source_bindings.yaml` (`pages://frontmost`,
  `numbers://frontmost`, `keynote://frontmost`, `applemail://**`,
  `macos://clipboard`, `macos://app/**`) so policy decisions and audits name
  the real local surface instead of an empty target.

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
