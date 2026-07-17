"""Daemon process lifecycle: start (run forever), stop (via socket), status."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import yaml

from capabledeputy.app import App
from capabledeputy.config.manifest import RuntimeManifest
from capabledeputy.daemon.agent_handlers import make_agent_handlers
from capabledeputy.daemon.approval_handlers import make_approval_handlers
from capabledeputy.daemon.artifact_handlers import make_artifact_handlers
from capabledeputy.daemon.audit_handlers import make_audit_handlers
from capabledeputy.daemon.bundle_handlers import make_bundle_handlers
from capabledeputy.daemon.demo_handlers import make_demo_handlers
from capabledeputy.daemon.devbox_handlers import make_devbox_handlers
from capabledeputy.daemon.extract_handlers import make_extract_handlers
from capabledeputy.daemon.gui_handlers import make_gui_handlers
from capabledeputy.daemon.handlers import default_handlers
from capabledeputy.daemon.image_ops_handlers import make_image_ops_handlers
from capabledeputy.daemon.mcp_admission_handlers import make_mcp_admission_handlers
from capabledeputy.daemon.memory_handlers import make_memory_handlers
from capabledeputy.daemon.onguard_handlers import make_onguard_handlers
from capabledeputy.daemon.pattern_handlers import make_pattern_handlers
from capabledeputy.daemon.policy_handlers import make_policy_handlers
from capabledeputy.daemon.programmatic_handlers import make_programmatic_handlers
from capabledeputy.daemon.relationship_handlers import make_relationship_handlers
from capabledeputy.daemon.scripting_handlers import make_scripting_handlers
from capabledeputy.daemon.security_context_handlers import make_security_context_handlers
from capabledeputy.daemon.server import Daemon
from capabledeputy.daemon.session_handlers import make_session_handlers
from capabledeputy.daemon.settings_store import load_settings
from capabledeputy.daemon.setup_control_handlers import make_setup_control_handlers
from capabledeputy.daemon.skill_handlers import make_skill_handlers
from capabledeputy.daemon.source_context_handlers import make_source_context_handlers
from capabledeputy.daemon.state_handlers import make_state_handlers
from capabledeputy.daemon.tool_handlers import make_tool_handlers
from capabledeputy.daemon.workstream_handlers import make_workstream_handlers
from capabledeputy.ipc.client import DaemonClient, DaemonNotRunningError
from capabledeputy.ipc.socket_path import default_socket_path
from capabledeputy.llm.factory import (
    default_llm_model_spec,
    make_llm_client,
    resolve_planner_model_spec,
)
from capabledeputy.llm.pool import ModelPool, require_mlx_on_apple_silicon
from capabledeputy.policy.capabilities import DEFAULT_MAX_DELEGATION_DEPTH
from capabledeputy.policy.overrides import OverridePolicies
from capabledeputy.secrets import load_anthropic_api_key
from capabledeputy.upstream.config import load_config_file
from capabledeputy.upstream.manager import UpstreamManager


def max_delegation_depth() -> int:
    """Configured max delegation chain depth (002 FR-006). Deterministic,
    operator-set: `CAPDEP_MAX_DELEGATION_DEPTH` env, else the default.
    A non-positive / unparseable value falls back to the default
    (fail-safe: never an unbounded chain)."""
    import os

    raw = os.environ.get("CAPDEP_MAX_DELEGATION_DEPTH")
    if raw is None:
        return DEFAULT_MAX_DELEGATION_DEPTH
    try:
        v = int(raw)
    except ValueError:
        return DEFAULT_MAX_DELEGATION_DEPTH
    return v if v > 0 else DEFAULT_MAX_DELEGATION_DEPTH


# Issue #2 — operator-set default for the per-turn agent-loop iteration
# cap. A request that omits `max_iterations` falls back to this; a
# non-positive / unparseable env value falls back to the built-in
# default (fail-safe: never an unbounded loop).
DEFAULT_AGENT_MAX_ITERATIONS = 50


def agent_max_iterations() -> int:
    """Configured default agent-loop iteration cap (Issue #2).

    Deterministic, operator-set via `CAPDEP_AGENT_MAX_ITERATIONS`; falls
    back to `DEFAULT_AGENT_MAX_ITERATIONS`. A per-request `max_iterations`
    (e.g. `/spawn --max-iters N`) still overrides this default."""
    import os

    raw = os.environ.get("CAPDEP_AGENT_MAX_ITERATIONS")
    if raw is None:
        return DEFAULT_AGENT_MAX_ITERATIONS
    try:
        v = int(raw)
    except ValueError:
        return DEFAULT_AGENT_MAX_ITERATIONS
    return v if v > 0 else DEFAULT_AGENT_MAX_ITERATIONS


DEFAULT_IDLE_SHUTDOWN_SECONDS = 60.0


def idle_shutdown_seconds() -> float | None:
    """How long the daemon may sit with no connected clients before
    shutting down. Default is 60 seconds so one-shot polling clients do
    not thrash the daemon between refreshes. Set
    CAPDEP_IDLE_SHUTDOWN_SECONDS=0/off/false to keep it resident."""
    import os

    raw = os.environ.get("CAPDEP_IDLE_SHUTDOWN_SECONDS")
    if raw is None:
        return DEFAULT_IDLE_SHUTDOWN_SECONDS
    normalized = raw.strip().lower()
    if normalized in {"0", "off", "false", "no", "never"}:
        return None
    try:
        value = float(normalized)
    except ValueError:
        return DEFAULT_IDLE_SHUTDOWN_SECONDS
    return value if value > 0 else None


# 003 Phase 1 T005: load_v09_configs is fail-closed (refuses daemon
# start on missing or unparseable file). Per-loader schema validation
# (e.g., labels.yaml category shape) lands per-feature in Phases 2+;
# T005 only enforces *presence + parseability* of the eleven files.

_V09_CONFIG_FILES_JSON: tuple[str, ...] = (
    "risk_register.json",
    "risk_preference.json",
)
_V09_CONFIG_FILES_YAML: tuple[str, ...] = (
    "purposes.yaml",
    "source_bindings.yaml",
    "relationship_groups.yaml",
    "expectations.yaml",
    "override_policy.yaml",
    "envelopes.yaml",
    "labels.yaml",
    "profiles.yaml",
    "rules.yaml",
)


class V09ConfigError(RuntimeError):
    """A v0.9 config file is missing or fails to parse. Fail-closed
    per Constitution Principle VI — the daemon must refuse to start
    rather than run with a partially configured policy oracle."""


def _resolve_v09_configs_dir(override: Path | None = None) -> Path:
    """Configs dir resolution: explicit arg > CAPDEP_CONFIGS_DIR env
    > `configs/` relative to cwd. Returned path is not required to
    exist here; presence is checked by load_v09_configs()."""
    import os

    if override is not None:
        return override
    env = os.environ.get("CAPDEP_CONFIGS_DIR")
    if env:
        return Path(env)
    return Path("configs")


def load_v09_configs(configs_dir: Path | None = None) -> dict[str, Any]:
    """Load every v0.9 operator-config file (003 FR-009, FR-015, FR-029,
    FR-030, FR-032, FR-033, FR-043, FR-046). Fail-closed on missing or
    unparseable file: raises V09ConfigError. Returns a dict keyed by
    filename stem with the parsed body so downstream loaders can split
    further validation per-feature."""
    base = _resolve_v09_configs_dir(configs_dir)
    if not base.is_dir():
        raise V09ConfigError(
            f"v0.9 configs dir not found at {base}; set CAPDEP_CONFIGS_DIR "
            "or run the daemon from a directory with a configs/ tree.",
        )

    loaded: dict[str, Any] = {}
    for name in _V09_CONFIG_FILES_JSON:
        path = base / name
        if not path.is_file():
            raise V09ConfigError(f"v0.9 config missing: {path}")
        try:
            loaded[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise V09ConfigError(f"v0.9 config unparseable: {path} — {e}") from e

    for name in _V09_CONFIG_FILES_YAML:
        path = base / name
        if not path.is_file():
            raise V09ConfigError(f"v0.9 config missing: {path}")
        try:
            loaded[path.stem] = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            raise V09ConfigError(f"v0.9 config unparseable: {path} — {e}") from e

    return loaded


def _resolve_daemon_config(config_path: Path | None) -> Path | None:
    """Daemon config file resolution (opt-in, fail-soft).

    Precedence:
      1. explicit `config_path` arg
      2. CAPDEP_CONFIG env var
      3. `~/.config/capabledeputy/daemon.yaml` (the user-local default,
         populated by `capdep imap-setup` and friends — opt-in because
         the file only exists once the operator has registered something)

    Returns the path only if it exists; loading external MCP servers is
    a deliberate operator action, never implicit, so when no source
    yields a file this returns None.
    """
    import os

    raw = config_path or (
        Path(os.environ["CAPDEP_CONFIG"]) if os.environ.get("CAPDEP_CONFIG") else None
    )
    if raw is not None:
        return raw if raw.is_file() else None

    # User-local default — present iff a setup command has registered.
    from capabledeputy.cli._managed_config import user_default_daemon_config_path

    user_default = user_default_daemon_config_path()
    if user_default.is_file():
        return user_default
    return None


def _read_daemon_config(path: Path) -> dict[str, Any]:
    """Parse a daemon config file (YAML or JSON) into a dict. Empty /
    unreadable → empty dict (the caller treats absent blocks as 'no
    config'). Block-specific loaders (sandbox, decision_inspectors) then
    apply their own fail-closed validation to the parsed body."""
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    if path.suffix in {".yaml", ".yml"}:
        import yaml

        raw = yaml.safe_load(text) or {}
    else:
        import json

        raw = json.loads(text) or {}
    return raw if isinstance(raw, dict) else {}


def _report_admission(manager: UpstreamManager) -> None:
    """Surface the strict-mode admission outcome to the daemon log so
    the operator can see which upstream tools were registered and which
    were refused (the WI-1 fail-closed rejections)."""
    import sys

    for adapter in manager.adapters:
        rejected = adapter.rejected_tools
        line = f"[upstream] {adapter.name}: registered ok"
        if rejected:
            line += f"; REFUSED {len(rejected)} unclassified: {sorted(rejected)}"
        print(line, file=sys.stderr)


def _report_runtime_manifest(manifest: RuntimeManifest) -> None:
    """Surface a compact normalized-config summary at daemon startup."""
    import sys

    validation = manifest.validate()
    summary = manifest.summary()
    print(
        "[manifest] "
        f"tools={summary['tools']} "
        f"upstream_servers={summary['upstream_servers']} "
        f"hooks={summary['hooks']} "
        f"warnings={len(validation.warnings)}",
        file=sys.stderr,
    )
    if validation.errors:
        for issue in validation.errors:
            print(
                f"[manifest] ERROR {issue.subject}: {issue.message}",
                file=sys.stderr,
            )
        raise RuntimeError("runtime manifest validation failed")


def build_policy_context_from_configs(
    configs_dir: Path | None = None,
    state_db_path: Path | None = None,
) -> Any:
    """Build a PolicyContext bus from the operator-curated configs.

    Called after `load_v09_configs()` has confirmed presence + parse;
    this constructs the typed registries the engine consumes at
    runtime. Each loader is best-effort here: a config that exists
    but doesn't declare any entries yields an empty registry rather
    than refusing — that's how a fresh deployment with stub configs
    runs without v2 features active until the operator declares
    real entries.

    Returns the PolicyContext or raises on a load failure that the
    daemon should not paper over (Principle VI fail-closed for any
    declared-but-malformed config).
    """

    from capabledeputy.policy.bindings import load as load_bindings
    from capabledeputy.policy.context import PolicyContext
    from capabledeputy.policy.decision_rules import load as load_rules
    from capabledeputy.policy.envelope import (
        load_envelopes,
        load_risk_preference,
    )
    from capabledeputy.policy.overrides import (
        OverrideGrantStore,
    )
    from capabledeputy.policy.overrides import (
        load as load_overrides,
    )
    from capabledeputy.policy.purposes import load as load_purposes
    from capabledeputy.policy.relationships import load as load_relationship_groups
    from capabledeputy.policy.resolution import load_profiles

    base = _resolve_v09_configs_dir(configs_dir)

    # Each loader is fail-closed on missing/unparseable per its own
    # contract. _resolve_v09_configs_dir + load_v09_configs already
    # verified presence; here we re-parse into typed objects.
    profiles = load_profiles(base / "profiles.yaml")
    rules_v2 = load_rules(base / "rules.yaml")
    # Bindings are opt-in: an empty configs/source_bindings.yaml means
    # "operator hasn't authored any yet" — bindings stays None so
    # decide() skips canonicalization. As soon as the operator declares
    # even one binding, the file's fail-closed semantics activate (any
    # URI outside the declared scopes refuses). This avoids the cold-
    # start gotcha where a fresh install with stub configs refuses all
    # egress, while still honoring FR-023 fail-closed once enabled.
    raw_bindings = load_bindings(base / "source_bindings.yaml")
    bindings = raw_bindings if raw_bindings.bindings else None
    overrides = load_overrides(base / "override_policy.yaml")
    envelope_set = load_envelopes(base / "envelopes.yaml")
    risk_pref = load_risk_preference(base / "risk_preference.json")
    purposes_path = base / "purposes.yaml"
    # Q1 (FR-030, 2026-05-25): pass the legacy risk_preference.json
    # path so purposes that omit `risk_preference_dial` fall back
    # to the legacy global value (transitional). Operator-visible
    # warning fires once if the legacy file is consulted.
    purposes = (
        load_purposes(
            purposes_path,
            legacy_risk_preference_path=base / "risk_preference.json",
        )
        if purposes_path.is_file()
        else None
    )

    # Cookbook P2.3 — load the RelationshipGroups registry. The
    # tool client uses it to resolve action targets (counterparties)
    # to group memberships at decide() time so the family/work-team
    # rules actually fire for known recipients. The path is kept
    # alongside the registry so add_member can persist back to it.
    rg_path = base / "relationship_groups.yaml"
    relationship_groups = load_relationship_groups(rg_path)

    # 003 — handle store + override grant store live in-process; no
    # disk-backed read here. Persistence layered on top later.
    from capabledeputy.patterns.reference_handle import ReferenceHandleStore

    # If a state_db_path is provided, the override grant store
    # persists to override_grants table and reloads on construction.
    grant_store = (
        OverrideGrantStore(db_path=state_db_path)
        if state_db_path is not None
        else OverrideGrantStore()
    )
    # FR-019 (amended) egress escalation — which data escalates communication
    # egress from approval to override (absent file ⇒ none; approval default).
    from capabledeputy.policy.egress_escalation import load_egress_escalation

    egress_override_categories, egress_override_tiers = load_egress_escalation(
        base / "egress_escalation.yaml",
    )
    return PolicyContext(
        rules_v2=rules_v2,
        bindings=bindings,
        egress_override_categories=egress_override_categories,
        egress_override_tiers=egress_override_tiers,
        override_policies=overrides,
        override_grants=grant_store,
        handle_store=ReferenceHandleStore(),
        envelope_set=envelope_set,
        risk_preference=risk_pref.value,
        profiles=profiles,
        relationship_groups=relationship_groups,
        relationship_groups_path=rg_path,
    ), purposes


def apply_posture_from_config(
    raw_cfg: dict[str, Any],
    policy_context: Any,
    configs_dir: Path | None = None,
) -> tuple[Any, list[str]]:
    """#305 — apply an operator `posture: <id>` daemon-config key.

    Selects a shipped preset (strict / high-security-useful /
    low-friction-practical) or a custom posture from configs/postures.yaml
    and BINDS the existing dials (a posture is a named value-binding, docs in
    policy/posture.py): risk_preference, clearance_max_tier (when the posture
    sets one), the flow-pattern defaults consumed by select_mode via
    `active_posture`, the projection-only knob, and the inspector_set filter
    over the configured decision inspectors.

    Fail-closed (Principle VI): an unknown id, a custom posture shadowing a
    preset id, an invalid postures.yaml, or an unknown inspector_set name
    raises — the daemon refuses to start. Absent `posture:` key ⇒ returned
    context is unchanged and every consumer keeps legacy behavior
    (select_mode heuristic, projection-only default, all configured
    inspectors active).

    Returns (policy_context, log_messages).
    """
    posture_key = raw_cfg.get("posture")
    if posture_key is None:
        return policy_context, []

    from capabledeputy.policy.decision_inspector_loader import (
        select_inspectors_for_posture,
    )
    from capabledeputy.policy.posture import load_postures, resolve_posture

    custom_path = _resolve_v09_configs_dir(configs_dir) / "postures.yaml"
    custom = load_postures(custom_path) if custom_path.is_file() else {}
    active = resolve_posture(str(posture_key), custom)
    # The posture's inspector_set names which inspectors are ACTIVE — it
    # filters the configured set, auto-instantiates known builtins with safe
    # defaults (warning), and fail-closes on unknown names.
    active_inspectors, inspector_warnings = select_inspectors_for_posture(
        policy_context.decision_inspectors,
        active.inspector_set,
    )
    policy_context = dataclasses.replace(
        policy_context,
        active_posture=active,
        decision_inspectors=active_inspectors,
        risk_preference=active.risk_preference,
        clearance_max_tier=(
            active.clearance_max_tier
            if active.clearance_max_tier is not None
            else policy_context.clearance_max_tier
        ),
    )
    messages = [f"[posture] WARNING: {w}" for w in inspector_warnings]
    messages.append(
        f"[posture] {active.id!r} active — "
        f"risk={active.risk_preference.value} "
        f"projection_only={active.projection_only} "
        f"inspectors=[{', '.join(active.inspector_set)}] "
        f"retention={active.retention.value} (recorded; retention "
        "enforcement lands with the daily-driver retention machinery)",
    )
    return policy_context, messages


def enforce_requirements_from_config(
    policy_context: Any,
    configs_dir: Path | None = None,
) -> list[str]:
    """#307 — verify the built-in hard requirements PLUS any operator custom
    requirements (`configs/requirements.yaml`) against the ACTIVE posture and
    its decision-inspectors, and RAISE `RequirementViolationError` if any is unmet —
    the daemon-start gate.

    A no-op (returns `[]`) when no posture is active: the requirement DSL is a
    posture-scoped guarantee, and an unconfigured legacy runtime keeps its
    prior startup behavior — the three built-in floors are STRUCTURALLY enforced
    by the engine regardless; this gate is the tripwire that proves the selected
    posture + inspectors don't undermine them. Fail-closed (Principle VI): an
    unparseable requirements.yaml or an unmet requirement refuses start.

    Requirements are checked against the loaded `rules_v2` (the policy context
    carries them) and the deployment's trust profile. NB: PolicyContext does not
    yet carry a static `trust_profile_is_personal`, so the startup gate reflects
    the DEFAULT MANAGED profile; the personal-profile check is reachable via the
    `verify_requirements(..., trust_profile_is_personal=True)` API for CI.
    TODO: thread personal-ness into the startup gate when it becomes available.
    """
    active = getattr(policy_context, "active_posture", None)
    if active is None:
        return []

    from capabledeputy.policy.requirements import (
        enforce_requirements,
        load_requirements,
    )

    req_path = _resolve_v09_configs_dir(configs_dir) / "requirements.yaml"
    custom = load_requirements(req_path) if req_path.is_file() else ()
    return enforce_requirements(
        posture=active,
        decision_inspectors=tuple(getattr(policy_context, "decision_inspectors", ()) or ()),
        clearance_max_tier=getattr(policy_context, "clearance_max_tier", None),
        custom=custom,
        trust_profile_is_personal=bool(
            getattr(policy_context, "trust_profile_is_personal", False),
        ),
        rules_v2=getattr(policy_context, "rules_v2", None),
    )


def overlay_unified_policy_from_config(
    policy_context: Any,
    configs_dir: Path | None = None,
) -> tuple[Any, list[str]]:
    """Migration step (epic #377): if a unified `configs/capdep.yaml` exists,
    compile it and OVERLAY the decision structures it declares onto the
    per-file-built PolicyContext — the authoring surface becomes the source of
    truth for the sections it authors, while unauthored sections keep the legacy
    per-file loaders (incremental adapter migration, design §10).

    Overlaid when declared: the decision `rules` and outcome `envelopes`.
    Validated at start via the #385 gate; a compile failure or any error-severity
    problem refuses daemon start (fail-closed, Principle VI).

    Absent `capdep.yaml` ⇒ no-op (returns the context unchanged), so existing
    per-file deployments are untouched.
    """
    path = _resolve_v09_configs_dir(configs_dir) / "capdep.yaml"
    if not path.is_file():
        return policy_context, []

    from capabledeputy.policy.authoring import ConfigError, load_config
    from capabledeputy.policy.policy_check import check_policy, has_errors

    compiled = load_config(path)  # fail-closed ConfigError propagates
    problems = check_policy(compiled)
    if has_errors(problems):
        lines = "\n".join(f"  - {p.where}: {p.message}" for p in problems if p.severity == "error")
        raise ConfigError(f"{path} failed policy check:\n{lines}")

    changes: dict[str, Any] = {}
    if compiled.rules.rules:
        changes["rules_v2"] = compiled.rules
    if compiled.envelopes.by_cell:
        changes["envelope_set"] = compiled.envelopes
    if not changes:
        return policy_context, []
    policy_context = dataclasses.replace(policy_context, **changes)
    return policy_context, [
        f"[capdep.yaml] unified policy active — overlaid {sorted(changes)} "
        f"({len(problems)} check note(s))",
    ]


async def run_daemon(
    socket_path: Path | None = None,
    state_db_path: Path | None = None,
    audit_log_path: Path | None = None,
    model: str | None = None,
    verbose: bool = False,
    policy_preview: bool | None = None,
    config_path: Path | None = None,
) -> None:
    import os

    # 003 T005: fail-closed v0.9 config load before any other work.
    # If any file is missing or unparseable, refuse to start (Principle VI).
    load_v09_configs()
    # 003 runtime activation — build the typed PolicyContext from the
    # loaded configs and inject it into the App. Without this, the
    # v2 four-axis pipeline stays dormant at runtime. The
    # OverrideGrantStore persists to the session store DB so grants
    # survive daemon restarts.
    from capabledeputy.paths import default_state_db_path

    effective_db = state_db_path or default_state_db_path()
    policy_context, purposes_registry = build_policy_context_from_configs(
        state_db_path=effective_db,
    )

    # Migration (epic #377): overlay a unified configs/capdep.yaml, when present,
    # onto the per-file-built context (opt-in; absent file ⇒ no-op).
    import sys as _sys_overlay

    policy_context, _overlay_messages = overlay_unified_policy_from_config(policy_context)
    for _m in _overlay_messages:
        print(_m, file=_sys_overlay.stderr)

    # 004 U034/U035: same config can declare a `sandbox:` block with
    # region specs for the Podman provider. Construct the actuator
    # BEFORE the App so it threads through PolicyContext into the
    # tool client (the EXECUTE.sandbox fail-closed gate reads
    # policy_context.sandbox_actuator_wired downstream).
    resolved_pre_app = _resolve_daemon_config(config_path)
    if resolved_pre_app is not None and policy_context is not None:
        import sys as _sys

        from capabledeputy.substrate.podman_sandbox import (
            PodmanSandboxActuator,
            load_sandbox_specs_from_file,
        )

        specs = load_sandbox_specs_from_file(resolved_pre_app)
        if specs:
            # Fail-closed: misconfigured sandbox is a hard error, never
            # a silent fall-through to the demo actuator.
            actuator = PodmanSandboxActuator(specs)
            # Same spec set drives the persistent devbox manager.
            # Both wire onto PolicyContext so the corresponding tool
            # makers can find them. PodmanNotAvailable from either
            # surfaces as a hard daemon-start failure.
            from capabledeputy.substrate.podman_devbox import PodmanDevbox

            devbox = PodmanDevbox(specs)
            policy_context = dataclasses.replace(
                policy_context,
                sandbox_actuator=actuator,
                devbox_manager=devbox,
            )
            print(
                f"[sandbox] PodmanSandboxActuator + PodmanDevbox wired with "
                f"{len(specs)} region spec(s): "
                f"{', '.join(s.spec_id for s in specs)}",
                file=_sys.stderr,
            )

    # Issue #46 — turn the decision-refinement layer ON. Load any
    # operator-authored `decision_inspectors:` from the daemon config
    # (builtins + Starlark scripts), compile them fail-closed, and attach
    # to the PolicyContext. Without this the chokepoint's
    # `decision_inspectors` stays empty and the Starlark host can't affect
    # a real decision (the dormant-layer gap from the alignment
    # assessment). A misconfigured entry refuses daemon start.
    if resolved_pre_app is not None and policy_context is not None:
        import sys as _sys_di

        from capabledeputy.policy.decision_inspector_loader import (
            load_decision_inspectors,
        )

        raw_cfg = _read_daemon_config(resolved_pre_app)
        inspectors = load_decision_inspectors(
            raw_cfg,
            base_dir=resolved_pre_app.parent,
        )
        if inspectors:
            policy_context = dataclasses.replace(
                policy_context,
                decision_inspectors=inspectors,
            )
            print(
                f"[policy] decision-refinement layer ON — "
                f"{len(inspectors)} inspector(s): "
                f"{', '.join(getattr(i, 'name', '?') for i in inspectors)}",
                file=_sys_di.stderr,
            )

        # #305 — security-posture selection from the daemon config.
        policy_context, posture_messages = apply_posture_from_config(
            raw_cfg,
            policy_context,
        )
        for message in posture_messages:
            print(message, file=_sys_di.stderr)

        # #307 — operator requirement DSL. Verify the built-in hard requirements
        # plus any configs/requirements.yaml against the active posture +
        # inspectors; RequirementViolationError refuses start (fail-closed).
        for message in enforce_requirements_from_config(policy_context):
            print(message, file=_sys_di.stderr)

    # Precedence: explicit arg (CLI flag) > CAPDEP_POLICY_PREVIEW env >
    # default on. The env var is off only for explicit falsey values.
    if policy_preview is None:
        env = os.environ.get("CAPDEP_POLICY_PREVIEW")
        enable_policy_preview = True
        if env is not None and env.strip().lower() in ("0", "false", "no", "off"):
            enable_policy_preview = False
    else:
        enable_policy_preview = policy_preview

    settings = load_settings()
    configs_dir = _resolve_v09_configs_dir()
    require_mlx_on_apple_silicon(prefer_local_mlx=settings.prefer_local_mlx)
    model_pool = ModelPool.from_config(configs_dir=configs_dir)
    model_pool.preload("planner.fast")
    chosen_model = model or resolve_planner_model_spec(
        prefer_local_mlx=settings.prefer_local_mlx,
    )
    warning_model = chosen_model or default_llm_model_spec()
    backend = os.environ.get("CAPDEP_LLM_BACKEND", "").strip().lower()
    quarantined_model = os.environ.get("CAPDEP_QUARANTINED_LLM_MODEL")
    # Populate ANTHROPIC_API_KEY from CLAUDEAPI.KEY in the cwd if an
    # Anthropic-backed model is selected. MLX defaults on macOS should
    # not emit an irrelevant missing-key warning.
    if (
        backend in {"litellm", "api", "anthropic"}
        or (not backend and "claude" in warning_model)
        or (quarantined_model and "claude" in quarantined_model)
    ):
        import sys as _sys_for_key

        pre_existing = bool(os.environ.get("ANTHROPIC_API_KEY"))
        loaded = load_anthropic_api_key()
        if pre_existing:
            print("[llm] ANTHROPIC_API_KEY found in environment", file=_sys_for_key.stderr)
        elif loaded:
            print(
                "[llm] ANTHROPIC_API_KEY loaded from ./CLAUDEAPI.KEY",
                file=_sys_for_key.stderr,
            )
        else:
            print(
                "[llm] WARNING: no ANTHROPIC_API_KEY in env and no CLAUDEAPI.KEY "
                "file in cwd — Anthropic-backed LLM calls will fail until you set one. "
                "Either `export ANTHROPIC_API_KEY=...` or drop the key in "
                "./CLAUDEAPI.KEY (will be auto-loaded next start).",
                file=_sys_for_key.stderr,
            )

    use_legacy_single_client = bool(
        backend
        or os.environ.get("CAPDEP_ALLOW_REMOTE_LLM", "").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    quarantined_client = None
    legacy_llm_client = None
    if use_legacy_single_client:
        legacy_llm_client = make_llm_client(chosen_model)
        if quarantined_model:
            quarantined_client = make_llm_client(quarantined_model)
        model_pool = None

    skills_env = os.environ.get("CAPDEP_SKILLS_DIR")
    skills_dir = Path(skills_env) if skills_env else None

    # Issue #5 — dynamic filesystem labeling. Load the operator's
    # fs_label_rules.yaml (absent ⇒ no-op labeler) so fs reads attach
    # Axis-A category labels and local-file data participates in IFC.
    from capabledeputy.policy.fs_labeling import load_fs_label_rules

    fs_labeler = load_fs_label_rules(_resolve_v09_configs_dir() / "fs_label_rules.yaml")

    app = App(
        state_db_path=state_db_path,
        audit_log_path=audit_log_path,
        llm_client=legacy_llm_client,
        quarantined_llm=quarantined_client,
        model_pool=model_pool,
        skills_dir=skills_dir,
        enable_policy_preview=enable_policy_preview,
        policy_context=policy_context,
        purposes=purposes_registry,
        fs_labeler=fs_labeler,
    )
    await app.startup()

    handlers = default_handlers()
    # Operator stats: register daemon.info so /server slash command
    # can show version + uptime + tool/session counts.
    from capabledeputy.daemon.handlers import make_info_handler

    handlers["daemon.info"] = make_info_handler(app)
    handlers.update(make_session_handlers(app.graph, app.session_coordinator, app.workstreams))
    handlers.update(make_devbox_handlers(app))
    handlers.update(make_relationship_handlers(app))
    handlers.update(make_security_context_handlers(app))
    handlers.update(make_skill_handlers(app))
    handlers.update(make_audit_handlers(app.audit))
    handlers.update(make_policy_handlers())
    handlers.update(make_tool_handlers(app.registry, app.graph, app.tool_client))
    handlers.update(make_agent_handlers(app))
    handlers.update(make_approval_handlers(app))
    handlers.update(make_pattern_handlers(app))
    handlers.update(make_memory_handlers(app))
    handlers.update(make_state_handlers(app))
    handlers.update(make_workstream_handlers(app))
    handlers.update(make_programmatic_handlers(app))
    handlers.update(make_bundle_handlers(app))
    handlers.update(make_source_context_handlers())
    handlers.update(make_artifact_handlers())
    handlers.update(make_scripting_handlers())
    handlers.update(make_image_ops_handlers(app))
    handlers.update(make_mcp_admission_handlers(app))
    handlers.update(make_gui_handlers(app))
    handlers.update(make_onguard_handlers(app))
    handlers.update(
        make_setup_control_handlers(
            app,
            source_bindings_path=_resolve_v09_configs_dir() / "source_bindings.yaml",
        ),
    )
    from capabledeputy.daemon.settings_handlers import make_settings_handlers

    handlers.update(make_settings_handlers(app, config_path=resolved_pre_app))
    # 003 US6 — override RPC handlers bridge the CLI to the daemon's
    # OverrideGrantStore + OverridePolicies. Without these, the
    # `capdep override` CLI mutates a process-local store that the
    # daemon never sees.
    if app.policy_context is not None and app.policy_context.override_grants is not None:
        from capabledeputy.daemon.override_handlers import make_override_handlers

        handlers.update(
            make_override_handlers(
                app.policy_context.override_grants,
                app.policy_context.override_policies or OverridePolicies(by_floor={}),
            ),
        )
    handlers.update(make_demo_handlers(app))
    handlers.update(make_extract_handlers(app))

    async def daemon_methods(params: dict[str, Any]) -> dict[str, Any]:
        return {"methods": sorted(handlers)}

    handlers["daemon.methods"] = daemon_methods

    daemon = Daemon(
        socket_path or default_socket_path(),
        handlers=handlers,
        verbose=verbose,
        idle_shutdown_seconds=idle_shutdown_seconds(),
    )
    app.daemon_server = daemon

    async def _relay_audit(event) -> None:
        await daemon.publish("audit", event.to_dict())

    app.audit.subscribe(_relay_audit)
    app.session_coordinator.set_publisher(daemon.publish)

    # Opt-in: if a daemon config file with an `upstream_servers:` section
    # is provided, spawn those MCP servers for the daemon's lifetime and
    # register their (policy-gated, labeled) tools into the SAME
    # app.registry every client uses — so capdep chat / console / tui see
    # them automatically, already gated. Absent config -> native only.
    resolved = _resolve_daemon_config(config_path)
    upstream_configs = load_config_file(resolved) if resolved is not None else []

    # Issue #35 — load per-server YAML files from servers.d/ and
    # register custom kinds globally so the chokepoint can match
    # them. Layout: alongside daemon.yaml in `~/.config/capabledeputy/`.
    # Loader is tolerant of missing directory (returns empty).
    from capabledeputy.policy.capabilities import register_custom_kind_registry
    from capabledeputy.upstream.server_yaml import (
        KindCollisionError,
        UnknownOverrideTargetError,
        apply_overrides,
        load_servers_d,
    )

    servers_d_dir = (
        resolved.parent / "servers.d"
        if resolved is not None
        else Path.home() / ".config" / "capabledeputy" / "servers.d"
    )
    try:
        per_server_configs, override_files, kind_registry = load_servers_d(servers_d_dir)
    except (KindCollisionError, UnknownOverrideTargetError) as e:
        # Fail loudly. A misconfigured servers.d/ is an operator
        # bug that needs to be visible, not silently ignored.
        import sys as _sys

        print(
            f"[daemon] FATAL servers.d/ error: {e}",
            file=_sys.stderr,
        )
        raise

    if per_server_configs:
        # Merge override files into their target server configs
        merged = apply_overrides(per_server_configs, override_files)
        # Promote per-server yamls' server_config to the upstream list
        # so they get spawned alongside any legacy `upstream_servers:`
        # entries. The kind registry is installed before any tool
        # registration runs, so custom kinds are visible to the
        # chokepoint from turn 1.
        upstream_configs = upstream_configs + [c.server_config for c in merged]

    # Always install the registry (even if empty) so policy code can
    # consult it without a None check.
    register_custom_kind_registry(kind_registry)

    if kind_registry.all():
        import sys as _sys

        print(
            f"[daemon] registered {len(kind_registry.all())} custom kind(s) from {servers_d_dir}",
            file=_sys.stderr,
        )

    # Issue #1: record our PID so `daemon stop` can fall back to
    # signal-based termination if the RPC shutdown path stalls (hung
    # upstream subprocess, orphaned-by-parent-shell, etc.). Removal in
    # the finally block — even an exception during serve() must not
    # leave a stale pidfile pointing at a dead process.
    #
    # Tests pass an explicit `socket_path` so run_daemon runs inside
    # the test process; writing the pidfile in that case would point
    # at the test runner's PID, which `stop_daemon`'s
    # signal-escalation path could end up sending SIGTERM to.
    # Production (CLI `daemon start`) passes socket_path=None.
    from capabledeputy.ipc.pidfile import remove_pidfile, write_pidfile

    using_default_socket = socket_path is None
    pidfile_written = False
    if using_default_socket:
        pidfile_path = write_pidfile()
        pidfile_written = True
        print(
            f"[daemon] pid={os.getpid()} pidfile={pidfile_path}",
            file=__import__("sys").stderr,
        )

    # Issue #34 — load the per-message email labeler (absent ⇒ no-op) and
    # hand it to the manager so Gmail (and any email-shaped) reads get
    # per-message Axis-A category labels on top of the server's floor.
    from capabledeputy.policy.email_labeling import load_email_label_rules

    email_labeler = load_email_label_rules(
        _resolve_v09_configs_dir() / "email_label_rules.yaml",
    )

    # Issue #13 — credential vault. Resolve each upstream server's secrets
    # from the mode-0600 vault and merge them into that server's spawn env,
    # so credentials stay out of the committed config and the daemon's
    # broad environment. The audit records only the vault REF, never the
    # value. (Per-call / env-echo resistance for a tool that dumps its own
    # env needs container isolation — #15/#16; documented in the vault.)
    if upstream_configs:
        from capabledeputy.audit.events import Event, EventType
        from capabledeputy.upstream.credential_vault import (
            default_vault_path,
            load_credential_vault,
        )

        vault = load_credential_vault(default_vault_path())
        if vault.entries:
            merged_configs = []
            for cfg in upstream_configs:
                secret_env = vault.env_for(cfg.name)
                if secret_env:
                    cfg = dataclasses.replace(cfg, env={**cfg.env, **secret_env})
                    await app.audit.write(
                        Event(
                            event_type=EventType.CREDENTIAL_INJECTED,
                            payload={
                                "server": cfg.name,
                                "refs": vault.refs_for(cfg.name),
                                "capability_kinds": list(
                                    vault.entries[cfg.name].capability_kinds,
                                ),
                            },
                        ),
                    )
                merged_configs.append(cfg)
            upstream_configs = merged_configs

    try:
        if upstream_configs:
            async with UpstreamManager(
                upstream_configs,
                app.registry,
                email_labeler=email_labeler,
            ) as manager:
                # Stash manager on app so /server (daemon.info RPC) can
                # read per-upstream-server status. App doesn't strongly
                # depend on the manager type — duck-typed `server_status`.
                app.upstream_manager = manager
                _report_admission(manager)
                runtime_manifest = RuntimeManifest.from_runtime(
                    registry=app.registry,
                    policy_context=policy_context,
                    upstream_servers=tuple(upstream_configs),
                )
                _report_runtime_manifest(runtime_manifest)
                handlers.update(
                    make_settings_handlers(
                        app,
                        config_path=resolved,
                        runtime_manifest=runtime_manifest,
                    ),
                )
                await daemon.serve()
        else:
            runtime_manifest = RuntimeManifest.from_runtime(
                registry=app.registry,
                policy_context=policy_context,
                upstream_servers=(),
            )
            _report_runtime_manifest(runtime_manifest)
            handlers.update(
                make_settings_handlers(
                    app,
                    config_path=resolved,
                    runtime_manifest=runtime_manifest,
                ),
            )
            await daemon.serve()
    finally:
        # Roadmap v2 #1 — drive App.shutdown so live devboxes get
        # torn down + background tasks cancelled before the process
        # exits. The serve() call returns when the shutdown event
        # fires (RPC) or when the task group is cancelled (signal).
        # Either way we want a clean App shutdown here.
        try:
            await app.shutdown()
        except Exception as _shutdown_err:
            import sys

            print(
                f"[shutdown] App.shutdown raised: {_shutdown_err}",
                file=sys.stderr,
            )
        if pidfile_written:
            remove_pidfile()


async def stop_daemon(socket_path: Path | None = None) -> bool:
    """Stop the running daemon.

    Issue #1 escalation: RPC shutdown is the preferred path because it
    lets the daemon clean up cleanly. But the daemon may be hung on
    upstream-subprocess teardown, orphaned by a parent shell, or just
    not responding. In those cases the pidfile gives us a reliable
    fallback: SIGTERM with a 5s grace, escalating to SIGKILL.
    """
    from capabledeputy.ipc.pidfile import (
        read_pidfile,
        remove_pidfile,
        terminate_with_escalation,
        wait_for_exit,
    )

    explicit_socket = socket_path is not None
    client = DaemonClient(socket_path or default_socket_path())
    target_pid = read_pidfile()

    # 1. Try the polite RPC path first.
    rpc_sent = False
    try:
        await client.call("shutdown")
        rpc_sent = True
    except DaemonNotRunningError:
        # Socket gone — either no daemon, or daemon is wedged with a
        # stale socket. If the pidfile points at a live process, the
        # signal-fallback below will handle it for the default daemon
        # socket. For explicit sockets, a global pidfile is not enough
        # evidence to terminate a process that may belong to another
        # daemon instance.
        pass

    if explicit_socket and not rpc_sent:
        return False

    # 2. If RPC succeeded and we know the PID, give the daemon a brief
    #    moment to exit cleanly. If we don't have a PID, trust the RPC
    #    return as success.
    if target_pid is None:
        return rpc_sent
    if rpc_sent and wait_for_exit(target_pid, timeout_seconds=5.0):
        remove_pidfile()
        return True

    # 3. Signal-based fallback. Either RPC failed, or the daemon
    #    didn't exit within the grace window — escalate.
    outcome = terminate_with_escalation(target_pid)
    remove_pidfile()
    # "already_gone" is success; the daemon exited between our checks.
    return outcome in ("already_gone", "term", "kill")


async def daemon_status(socket_path: Path | None = None) -> dict[str, Any]:
    """Report daemon liveness. RPC ping is authoritative — if it
    succeeds, the daemon is responsive. The pidfile reads as a
    secondary signal: useful for diagnostics when the socket has
    desynced from the actual process state (which is exactly the
    failure mode Issue #1 fixed)."""
    from capabledeputy.ipc.pidfile import read_pidfile

    client = DaemonClient(socket_path or default_socket_path())
    pid = read_pidfile()  # already cleans up stale pidfiles
    try:
        result = await client.call("ping")
    except DaemonNotRunningError:
        return {"running": False, "pid": pid}
    return {"running": True, "ping": result, "pid": pid}
