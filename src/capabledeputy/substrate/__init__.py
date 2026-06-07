"""003 substrate ports (interfaces only).

This package declares ports the TCB consumes (per Constitution
Principle VII: external substrate behind in-repo ports). Provider
implementations live in spec 004. Importing a port without an
implementation raises NotImplementedError at first call — never
silently best-effort.

Members:
- source_port.py      (port + GitSourcePort provider — git_source.py; more
  backends via `get_source_port(kind)`)
- version_write_port.py (port + GitVersionedWritePort provider —
  git_versioned_write.py; more backends via `get_versioned_write_port(kind)`)
- sandbox_actuator.py (port; concrete impl DONE — PodmanSandboxActuator in
  podman_sandbox.py, the ephemeral `EXECUTE.sandbox` runtime. Its
  persistent counterpart for `EXECUTE.devbox` is PodmanDevbox in
  podman_devbox.py — the two are complementary, not alternatives.)
- policy_script_host.py (port + SafePythonScriptHost ref + StarlarkScriptHost
  sandbox — DONE; Starlark via the optional capabledeputy[starlark] extra)
- inspector_port.py   (T121 — Phase 2a, here)

Candidate future providers (what to add behind each port, why, and the
value to a typical user's workflow):
``specs/004-mcp-and-substrate/substrate-provider-candidates.md``.
"""
