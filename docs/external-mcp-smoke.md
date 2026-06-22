# External MCP Smoke Matrix

Real upstream MCP server smoke tests are opt-in because they depend on local
server binaries, network access, credentials, and account state. The default
test suite uses deterministic fake servers instead.

To run the matrix:

```bash
CAPDEP_REAL_MCP_SMOKE_CONFIG=/path/to/real-mcp-smoke.yaml \
  uv run pytest tests/test_external_mcp_smoke_matrix.py -m external_mcp
```

The config file uses the same `upstream_servers` schema as the daemon. Keep
labels and disabled capabilities explicit so the smoke test exercises CapDep's
security model instead of just checking process startup.

Example:

```yaml
upstream_servers:
  - name: github
    transport: stdio
    command: ["github-mcp-server", "stdio"]
    inherent_labels: ["external-untrusted"]
    strict: true
    disabled_kinds: ["SEND_EMAIL", "EXECUTE_DESTRUCTIVE"]
  - name: fetch
    transport: stdio
    command: ["uvx", "mcp-server-fetch"]
    inherent_labels: ["external-untrusted"]
    strict: true
```

The smoke matrix verifies:

- every configured server reports a registered or failed startup status;
- registered servers either register classified tools or reject tools
  fail-closed;
- registered tools are prefixed by server name and have capability
  classification plus inherent labels;
- failures are surfaced with status and error text instead of silently
  disappearing.
