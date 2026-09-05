# Stable MVP Release Checklist

**Last refreshed:** 2026-09-05

Use this checklist before calling CapableDeputy "stable MVP". If an item is not
true, the release is either incomplete or must explicitly document the
exception.

## Product scope

- [ ] `#319` is closed and daemon reconnect works across the supported client
  surfaces.
- [ ] `#331`, `#332`, `#333`, and `#334` are closed, so CapDepMac can resolve
  override/approval/onboarding/media flows without terminal-only fallbacks.
- [ ] `#325` and `#328` are closed, so the real-account Google Workspace and
  GitHub path is first-class and readiness is surfaced honestly.
- [ ] No `v0.60+` feature work is being presented as part of the MVP.

## Truth-source consistency

- [ ] `pyproject.toml`, `src/capabledeputy/version.py`, tags, README, roadmap,
  and changelog agree on the current release line.
- [ ] The current active milestone split in docs matches the live GitHub
  tracker.
- [ ] Historical RFC/spec docs are marked as draft/historical where they are
  no longer the active authority.

## Test and assurance gates

- [x] Default deterministic suite is green (3,407 passed; see evidence record).
- [x] Coverage ratchet is green.
- [x] Required workflow-pressure and security-alignment probes are green.
- [x] Isolated live-daemon lifecycle and client tests are green.
- [ ] Required GUI-sensitive smokes are green on a machine with the needed
  macOS permissions.
- [ ] Any release claim about a contract or model is backed by a linked test or
  the exception is documented explicitly.

## Architecture and contracts

- [ ] No trusted state mutation is performed directly by clients outside daemon
  RPC.
- [ ] Agent-callable tools are mediated by the single policy-gated dispatch
  path.
- [x] The active contract set is indexed in
  [design-traceability.md](design-traceability.md).
- [ ] Any changed contract docs ship with matching test updates.

## Release note honesty

- [ ] Deferred work is named explicitly, not silently implied as shipped.
- [ ] Credential-gated features are described as credential-gated if final
  acceptance still depends on real accounts or external state.
- [ ] Any remaining assurance gaps are listed as standing boundaries, not
  buried in code comments.


## Current acceptance record

See [stabilization-evidence-2026-09-05.md](stabilization-evidence-2026-09-05.md)
for exact checks and limitations. A build or passing protocol probe is not a
GUI workflow pass. GitHub issues remain open until their acceptance is verified.

- [ ] Research memo completes through the GUI and preserves citations/provenance.
- [ ] Local-document summary/revision completes through reviewed file effects.
- [ ] Inbox triage produces drafts against a real account without an implicit send.
- [ ] Seven days of dogfooding logged in [dogfood-log.md](dogfood-log.md).
