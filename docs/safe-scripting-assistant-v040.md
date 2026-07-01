# v0.40 Safe Practical Scripting Assistant

v0.40 reframes the code-workspace milestone around a narrower product promise:
CapDep can help non-programmers solve practical file and automation tasks with
small scripts while preserving daemon-owned policy, sandboxing, exact review
artifacts, labels, provenance, and audit.

Target workflows:

- Generate a short Python, shell, or Node script for a concrete user task.
- Run that script in an isolated region before touching user files.
- Return typed artifacts for the reviewed script, run evidence, and any file
  export or patch the user may approve.
- Canonicalize script workspace inputs and destinations so approvals bind to
  stable IDs, not model-invented paths.
- Treat git commit and PR flows as an advanced export path, not the core user
  value of v0.40.

Non-goals:

- CapDep does not become a generic autonomous coding agent clone.
- Scripts do not receive raw broad filesystem authority by default.
- Generated code is not applied to user files without a typed artifact and an
  approval bound to the exact content and destination.

Implementation closeout:

- `ScriptWorkspaceSourcePort` canonicalizes local script workspace paths and
  fails closed on root escapes.
- Script, script-run, and file-export artifact types bind generated code,
  sandbox evidence, and proposed outputs to exact hashes and destinations.
- Focused tests cover canonical workspace IDs, secret-like label elevation,
  exact artifact approval binding, run evidence artifacts, and file export
  artifacts.
