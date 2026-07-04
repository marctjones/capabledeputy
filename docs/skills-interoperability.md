# Skills interoperability

CapDep imports local `SKILL.md` packages as untrusted extensions. They can add
workflow guidance, quarantined-LLM tools, or sandboxed scripts, but they do not
gain operator authority and they cannot bypass the policy engine.

## Package formats

`CAPDEP_SKILLS_DIR` may contain both formats:

- Flat CapDep skill files: `*.md`
- Folder packages: `<skill-name>/SKILL.md`, with optional `references/`,
  `scripts/`, `assets/`, and `agents/` subdirectories

Folder packages are compatible with the Codex/Claude style of keeping
supporting material next to `SKILL.md`. CapDep indexes package resources and
reports invalid packages through diagnostics instead of silently ignoring them.

## Modes

Each skill has one explicit mode:

- `guidance`: session guidance only; default for folder packages
- `tool`: policy-gated tool; default for flat files
- `hybrid`: both guidance and tool

Guidance is returned as untrusted session context. It is not system/developer
instruction text and does not grant capabilities.

## Tool execution

Tool-mode skills without scripts run through the quarantined LLM path. They
preserve the existing skill behavior:

- YAML frontmatter declares `capability_kind`, `target_arg`, parameters, and
  inherent labels.
- Tool calls are denied or allowed by the same registry and policy machinery as
  native tools.
- Inherent labels propagate onto the tool result.
- Schema skills use the quarantined extractor path.

If no quarantined LLM is configured, guidance packages are still visible, but
LLM-backed tools are skipped with a `missing-quarantined-llm` diagnostic.

## Script execution

Skills may declare scripts in frontmatter:

```yaml
scripts:
  - path: scripts/run.py
    language: python
    spec_id: python-sandbox
```

Script skills never execute package files as host subprocesses. CapDep reads
the script bytes, creates an isolation region through the configured sandbox
actuator, sends the script as an input file, runs it inside the sandbox, and
discards the region afterward. Script tools always advertise
`EXECUTE_SANDBOX` with `spec_id` as the capability target.

Region creation and discard are audit events. If no sandbox/container actuator
is configured, script execution is refused.

## Diagnostics

The daemon exposes skill registry state over RPC:

- `skill.list`
- `skill.show`
- `skill.guidance`
- `skill.diagnostics`

The CLI exposes the same surface:

```bash
capdep skill list
capdep skill show <name> --body
capdep skill guidance <name> --session <session-id>
capdep skill diagnostics
```

`docs/client-parity.json` records daemon/CLI support as implemented and marks
TUI, CapDepMac, and MCP-control UI support as intentional follow-up surfaces.

## Validation

The standard skills tests cover:

- Flat and folder package parsing
- Guidance/tool/hybrid mode behavior
- Resource discovery
- Duplicate and degraded loading
- Quarantined-LLM refusal behavior
- Script refusal without a sandbox
- Script execution only through the sandbox actuator
- Guidance audit events
- Client parity entries
