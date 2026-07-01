# Substrate provider candidates — what to add behind the ports, and why

CapableDeputy's substrate is hexagonal: the trusted core calls in-repo
**ports** (ABCs/Protocols), and **providers** plug in behind them via a
registry, selected by `kind` (e.g. from `daemon.yaml`). Adding a provider
never touches the chokepoint.

Today's providers:

| Port | Provider(s) shipped | Registry |
|---|---|---|
| `SandboxActuator` (`EXECUTE.sandbox`) | `PodmanSandboxActuator` | wired in `app`/`daemon.lifecycle` |
| persistent devbox (`EXECUTE.devbox`) | `PodmanDevbox` | wired likewise |
| `VersionedWritePort` (FR-044) | `GitVersionedWritePort` | `get_versioned_write_port(kind)` |
| `SourcePort` (FR-048) | `GitSourcePort` | `get_source_port(kind)` |
| `PolicyScriptHost` | `SafePythonScriptHost` (ref), `StarlarkScriptHost` (sandbox) | `get_script_host(kind)` |

This note lists the *next* providers worth building, why each matters,
and what it buys a typical user — someone whose personal agent already
reads/writes their **files, email, calendar, notes, cloud docs**, makes
**purchases**, and **runs code** (the native tool surfaces).

## The two mechanics that make a provider valuable

1. **`VersionedWritePort` → autonomy.** A write that the port proves is
   reversible (it surfaced a retained prior version + attestation, and
   the verifier read the prior hash back) earns `reversible/system` via
   `verify_write_discipline`. The engine lets the agent perform a
   `reversible/system` write **without a human prompt**, while an
   unverifiable/irreversible write stays gated. So *every new
   VersionedWritePort backend turns another write surface from
   "always ask" into "act, but undoably."*
2. **`SourcePort` → safety (anti-confused-deputy).** Canonical ids bind
   policy + audit to an authoritative destination identity instead of
   the raw string the model typed (FR-048). *Every new SourcePort
   backend hardens another surface against model-typed-target
   redirection* (e.g. "reply to the invoice email" can't be steered to a
   different message).
3. **`SandboxActuator` → containment.** Ephemeral, egress-free execution
   means the agent can run code without it touching the host/network.
   *New actuators add stronger or heavier isolation tiers.*

## `VersionedWritePort` candidates

| Provider | What it does | Why / gap it fills | Typical-user value |
|---|---|---|---|
| **git** ✅ | commits each write; prior version = `commit:path` | local, content-addressed, zero infra | agent edits code/notes in a repo; any mistake is `git revert`-able, so the agent edits autonomously |
| **Google Drive (revisions API)** | update a Drive file; Drive keeps revisions | the agent already touches Drive docs, but a raw Drive write is *irreversible* to the policy → always prompts | "keep my project doc updated" — agent edits the live doc unprompted because Drive's revision history makes it reversible; user restores a bad edit from Drive's own version pane |
| **Microsoft 365 / SharePoint / OneDrive (version history)** | write with built-in document versioning | same as Drive for M365 shops | same value for Office users |
| **S3 with versioning + Object Lock** | write an object; prior versions retained, optionally WORM-locked for a retention window | shared cloud artifacts + *compliance-grade* immutable retention | agent publishes reports/exports to a team bucket; retention is provable (good for regulated work), writes stay reversible |
| **Local FS snapshot (btrfs/ZFS snapshot, or copy-on-write `.versions/`)** | snapshot the file before overwrite | repos cover git dirs; this covers the *rest* of the user's files | agent reorganizes/edits files outside a repo and those edits are still undoable |
| **SQLite/Postgres temporal (shadow-history table / system-versioning)** | structured-record writes with row history | structured stores (the memory tool, app data) get the same reversibility guarantee | agent updates a structured note/record store; prior rows are retained for rollback |

## `SourcePort` candidates

| Provider | What it does | Why / gap it fills | Typical-user value |
|---|---|---|---|
| **git** ✅ | path → stable `git:<repo-relative>` id | local canonical paths | path-confusion writes blocked in repos |
| **Gmail / IMAP** | message/thread ref → immutable `Message-ID`/thread-id | inbox content is *untrusted-external*; a model-typed reference is the classic confused-deputy lever | agent triages mail: "reply to the email about the invoice" binds to the real message id; an injected instruction can't redirect the reply to another thread; the audit logs the true message id |
| **Google Drive / SharePoint** | name/path → stable file/resource id | files get renamed/moved; names are model-spoofable | "update the budget doc" resolves to the canonical file id regardless of name/path; prevents writing to the wrong (or attacker-named) file |
| **Calendar** | event ref → canonical event id | "my 3pm" is ambiguous/model-typed | "move my 3pm to Thursday" binds to the actual event id, not a fuzzy match the model picked |
| **Contacts / relationship directory** | recipient → vetted principal id (feeds `RelationshipGroups`) | recipient addresses are exactly what egress rules gate on | "email Maria the summary" resolves to a vetted contact, not an address the model invented — pairs with the family/work-team egress rules |
| **S3** | bucket/key normalization | cloud object targets | canonical object identity for writes/reads to object storage |

## `SandboxActuator` candidates

| Provider | What it does | Why / gap it fills | Typical-user value |
|---|---|---|---|
| **Podman** ✅ | rootless `podman run --rm`, egress-free | host-free disposable execution, no daemon | agent runs code/data-analysis disposably; nothing touches the host or network |
| **Docker** | same UX via Docker | environments without rootless Podman | drop-in where Podman isn't installed |
| **gVisor (`runsc`)** | container UX + syscall interception | stronger kernel-attack-surface reduction than vanilla containers | running *genuinely* untrusted code (e.g. something fetched from the web) with a harder boundary |
| **Firecracker microVM** *(scheduled, v0.37+)* | VM-level isolation per run | hardware-grade isolation for high-risk execution | the strongest tier when the user runs something they really don't trust |
| **Modal (cloud serverless)** *(scheduled, v0.37+)* | remote sandbox with scale/GPUs | the user's laptop can't run heavy/long/GPU jobs | "analyze this big dataset / fine-tune this" runs off-device, still contained + egress-controlled |

## `PolicyScriptHost` candidates

| Provider | What it does | Status |
|---|---|---|
| **SafePythonScriptHost** | AST-filtered Python reference — NOT a boundary | ✅ (prototyping only) |
| **StarlarkScriptHost** | language-level sandbox (starlark-rust/PyO3) | ✅ (the real boundary) |
| **Lua (embedded)** | alternative embeddable policy language | candidate, low priority — Starlark already covers the need |
| **WebAssembly host** | wasm policy runtime | **removed** — Starlark covers it at lower complexity |

(A wasm *execution* actuator for pure-compute is a separate, lower-priority
idea from the dropped wasm *policy* host.)

## How to add one

1. Implement the port ABC in a new `substrate/<provider>.py`
   (e.g. `gdrive_versioned_write.py`).
2. Add a branch to the relevant registry (`get_versioned_write_port` /
   `get_source_port` / `get_script_host`), lazily importing the module.
3. Fail-closed on construction (missing creds/binary → typed error) and
   on bad input (uncanonicalizable target → raise, never guess).
4. For `VersionedWritePort`, make sure `read_prior_version_hash` returns
   the *exact* pre-write hash so `verify_write_discipline` can grant
   `reversible/system`.
5. Tests + (for execution/sandbox providers) a threat-model note like
   `starlark-policy-host-threat-model.md`.

The TCB never changes — only config selects the provider.
