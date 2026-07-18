# Spike #316 — Is the Podman sandbox a hard v1.0 dependency?

**Status:** resolved · **Date:** 2026-07-18 · **Affects:** v0.55/v0.58/v0.62
**Question:** Pattern 5 (SEALED) and restricted-tier handling lean on Podman,
which is opportunistic today and adds install burden. Is Podman a *hard* v1.0
dependency, or opportunistic with graceful degradation?

## TL;DR decision

**Opportunistic with graceful degradation — NOT a hard v1.0 dependency.**

- Podman is **not required** to run the daily driver.
- When Podman is absent, the runtime degrades to **Pattern 3 (REFERENCE /
  handle-routing)** — still safe: **less isolation, not less policy
  enforcement** — and announces the reduced capability clearly to the operator.
- Ship a **guided install** for users who want SEALED / Pattern 5 / full
  restricted-tier isolation.
- One fail-closed exception is preserved: a *restricted-tier* session that has
  **neither** Pattern 3 handles **nor** a Pattern 5 actuator is refused
  (FR-047). Degradation never crosses that floor.

Rationale: hard-requiring Podman adds significant install burden for a personal
daily driver; graceful degradation preserves the security model with a clear
capability announcement. This is a **reversible engineering tradeoff** — nothing
in the policy model forbids later hard-requiring Podman for a hardened profile.

## What is already true vs. what #316 ratifies

The bulk of the degradation shape **already exists** in the codebase. #316's job
is to *ratify it as the v1.0 stance* and confirm the operator-facing guidance and
capability announcement exist — not to build a new fallback. The parts already
shipped are marked ✅ below; the parts #316 leans on for the daily-driver story
are the setup guidance and the operator announcement.

## Pattern 3 (REFERENCE) vs Pattern 5 (SEALED) in this codebase

Both are `ExecutionMode` values in `mode/dispatcher.py`:

- **`REFERENCE` (Pattern 3, "handle-routing")** — line 41. The planner sees only
  reference-handle ids; the dispatcher binds the real values *after* `decide()`.
  The planner stays **data-blind** without needing any container. Availability =
  the tool surface offers an `accepts_handles=True` tool
  (`tool_surface_offers_handles`, 358). **No Podman required.**
- **`SEALED` (Pattern 5)** — line 46. Effects run "inside a disposable isolation
  region (containment lifts effective reversibility to reversible/system).
  **Requires a SandboxActuator port to be wired; if not, spawn refuses**." The
  wired SandboxActuator in production is `PodmanSandboxActuator`. Availability =
  `has_sandbox_actuator and visible_tool_surface_offers_sandbox(...)` (152–155).

The restricted-tier floor `select_mode_for_restricted` (269–300) encodes the
preference order that makes graceful degradation safe: it **prefers REFERENCE**
when a handle-aware tool is visible (287–291), **falls back to SEALED** only when
no handles exist but a SandboxActuator is wired (292–296), and **raises
`ModeSelectionError`** when *neither* is available (297–300, fail-closed FR-047).
Because REFERENCE is preferred and needs no container, restricted-tier work
"still runs via Pattern 3 handle-routing" without Podman — this is exactly the
init message quoted below.

For NONE/SENSITIVE/REGULATED sessions, `_achievable_mode` (312–355) downgrades a
posture's desired pattern to the strongest the tool surface supports
(SEALED → REFERENCE → DUAL_LLM → TURN_LEVEL) "rather than erroring" (326) — these
tiers never *need* Podman at all. ✅

## How Podman is used and probed (evidence)

| Surface | File / function | Behavior when Podman absent |
|---|---|---|
| Disposable one-shot sandbox | `substrate/podman_sandbox.py` `PodmanSandboxActuator.__init__` → `_verify_podman_present` (203–225) | **Fails closed at construction** — raises `PodmanNotAvailable` (203–207) *only if the actuator is being constructed*. `podman --version` returncode probe (212–225). |
| Persistent devbox | `substrate/podman_devbox.py` `PodmanDevbox.__init__` (106) → `_verify_podman_present` (430) | Same fail-closed construction guard (`PodmanNotAvailable`, 119–124). |
| Port interface | `substrate/sandbox_actuator.py` `SandboxActuator(ABC)` (89) | Docstring: invoking `EXECUTE.sandbox` with no actuator wired MUST refuse with `OverrideRequired` (6–10). |
| Non-Podman providers | `substrate/command_sandbox.py` (Modal/Firecracker, behind the same port) | `SandboxProviderUnavailableError` probe (34, 88–93) — also fail-closed, not degrade. Does **not** use Podman. |
| Demo stub | `substrate/in_process_sandbox.py` `InProcessSandboxActuator` (36) | **Never** used as a Podman fallback — "DEMO/TEST ONLY… does NOT provide actual isolation" (1–21). `is_demo_actuator` (113) lets CI refuse to deploy it. |

The key subtlety: **construction is only attempted when the operator has
declared a `sandbox:` block.** `podman_sandbox.py` `parse_sandbox_config`
(615–674) returns an **empty tuple** when the block is absent or `provider` is
not `podman` (641–646). No block ⇒ no specs ⇒ no actuator constructed ⇒ Podman is
never probed and never required.

### Actuator selection / wiring (evidence)

Selection lives in `daemon/lifecycle.py` (593–629), **not** `app.py` directly:

- `specs = load_sandbox_specs_from_file(...)` (607). If **no** `sandbox:` block,
  `specs` is empty → **no actuator wired** (`sandbox_actuator` stays `None`),
  daemon starts normally. ✅ (opportunistic path)
- `if specs:` → construct `PodmanSandboxActuator(specs)` + `PodmanDevbox(specs)`
  (608–618). The comment (609–610): "Fail-closed: misconfigured sandbox is a
  hard error, never a silent fall-through to the demo actuator." A declared block
  with Podman missing ⇒ **hard daemon-start failure** via `PodmanNotAvailable`,
  by design — an operator who *asked* for isolation is never silently downgraded.
- `app.py` reads `policy_context.sandbox_actuator`; its degradation is at the
  **tool-surface** level: `make_sandbox_tools` (`tools/native/sandbox.py` 56)
  returns `[]` when `policy_context.sandbox_actuator is None` (68–69), so on a
  Podman-less install the `sandbox.run` / devbox tools simply **don't appear**
  and the reaper stays off (`_maybe_start_devbox_reaper`, guarded). ✅

So there are two distinct states, both coherent:

1. **No `sandbox:` block (the daily-driver default):** Podman never touched;
   SEALED tools absent; restricted-tier routes through Pattern 3 REFERENCE.
   Graceful. ✅
2. **`sandbox:` block declared but Podman missing:** hard fail at daemon start
   (an explicit request for isolation must not be silently unmet). Fail-closed. ✅

Graceful degradation is state 1; state 2 is the deliberate "you asked for it, it
isn't there" guard.

## Setup / install guidance (evidence)

Podman is presented as **optional, with a guided install** — never a hard
prerequisite. `cli/init_cmd.py` `_sandbox_readiness_line` (46) surfaces readiness
during `capdep init` without ever writing the block, and emits three
operator-facing messages:

- **Ready** (61–65): "Sealed sandbox (Pattern 5) available — run `capdep-setup
  sandbox --apply` to enable SEALED, egress-free execution for restricted-tier
  work."
- **Installed but VM down** (67–71): "Podman is installed but its machine is not
  running — `podman machine start`, then `capdep-setup sandbox --apply`…"
- **Not installed** (72–76): "Sealed sandbox (Pattern 5) is optional: install
  Podman (`brew install podman`) then `capdep-setup sandbox --apply`. **Without
  it, restricted-tier work still runs via Pattern 3 handle-routing.**"

`cli/setup_cli.py` `sandbox_command` (286) makes Pattern 5 reachable only on an
explicit `--apply` ("Register the sandbox block when Podman is ready.", 290) and
"Reports install/start steps when Podman is not ready." (301–303). The readiness
probe `podman_readiness` (`_managed_config.py` 1058) returns
`ready | machine_not_running | not_installed` (uses `podman --version` then
`podman info`, since `--version` succeeds on macOS even with the VM down).
`setup_sandbox` (`cli/setup_domains.py` 178) "NEVER claims SEALED is reachable
without a real, running Podman (the in-process actuator is a test double… never
wired here)" (184–188).

## Consequences for setup and the restricted-tier default

- **What setup should say:** keep the three-state readiness message (ready /
  VM-down / not-installed) as the ratified operator UX, and keep the explicit
  "Without it, restricted-tier work still runs via Pattern 3 handle-routing"
  reassurance in the not-installed branch (`init_cmd.py` 72–76). Podman install
  is *guided and opt-in* (`capdep-setup sandbox --apply`), never a blocking step
  of `capdep init`. This is the guided-install deliverable and it already exists.
- **Restricted-tier default without Podman:** a restricted-tier session
  **routes through Pattern 3 REFERENCE** as long as a handle-aware tool is
  visible — `select_mode_for_restricted` prefers REFERENCE over SEALED (287–296),
  so the common daily-driver restricted flows do not need a container. The single
  fail-closed case remains: restricted-tier with **neither** Pattern 3 handles
  **nor** a Pattern 5 actuator ⇒ `ModeSelectionError`, spawn refused (297–300).
  Degradation lowers *isolation strength* (SEALED → REFERENCE), never *policy
  enforcement* — the same DENY floors and label gates apply either way.
- **Capability announcement the operator sees:** the reduced capability is
  announced on two surfaces that already exist — (1) the `capdep init` readiness
  line (`init_cmd.py` 61–76) at setup time, and (2) the daemon startup log
  `"[sandbox] PodmanSandboxActuator + PodmanDevbox wired with N region spec(s)"`
  (`daemon/lifecycle.py` 624–628) when SEALED *is* wired, whose **absence** is the
  signal that the install is running Pattern-3-only. #316's remaining polish is to
  make sure the reduced-capability state is legible at runtime (e.g. `capdep
  doctor` surfacing "SEALED unavailable — restricted-tier work uses Pattern 3
  handle-routing; install Podman to enable Pattern 5"), so an operator is never
  surprised that SEALED is silently off.

## Why this is reversible

Nothing in the policy model *requires* Podman; it is one provider behind the
`SandboxActuator` port (`command_sandbox.py` shows Modal/Firecracker behind the
same port). If a future hardened profile wants to hard-require isolation, it does
so by *declaring a `sandbox:` block* — which already turns "missing Podman" into a
fail-closed daemon-start error (state 2 above). So the permissive daily-driver
default and a future mandatory-isolation profile coexist without a code fork: the
`sandbox:` block is the single switch between "opportunistic" and "required."
