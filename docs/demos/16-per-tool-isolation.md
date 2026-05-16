# Demo 16: Per-Tool Container Isolation

**Audience:** the operations engineer asking what happens when an
upstream MCP server is compromised.
**Time:** ~2 minutes.
**Requires:** nothing beyond `uv sync`.

Beyond v0.2's all-in-one daemon container, every upstream MCP server
can run in its own podman container with policy-driven networking
and filesystem views. A compromised filesystem MCP server cannot
reach the network. A compromised fetch MCP server cannot read your
home directory.

The runtime wraps the upstream command in a podman invocation with
strict defaults; MCP transport stays stdio so `LabeledMcpAdapter` is
unchanged.

## What the demo proves

1. The default isolation profile drops every Linux capability,
   makes the root filesystem read-only, denies all networking, and
   runs as the rootless uid 1500.
2. Volume mounts default to read-only. Writable mounts require an
   explicit `ro: false` (visible in `git diff` for code review).
3. Bridge networking with an `allowed_hosts:` list pins specific
   destinations. Default-deny otherwise.
4. The systemd quadlet generator produces a `.container` unit with
   matching directives so users who prefer systemd-managed isolation
   don't lose anything.
5. YAML config rejects unknown network modes loudly.

## Walkthrough

```bash
uv run pytest tests/test_e2e_isolation.py -v
```

### Strict defaults

```python
iso = ContainerIsolation(image="alpine")
argv = iso.to_argv_prefix()
# Includes: --network=none --read-only --cap-drop=ALL
#           --security-opt=no-new-privileges --user=1500:1500
```

The defaults are biased toward least privilege. Users opt INTO each
relaxation:

| Capability needed | YAML opt-in |
|---|---|
| Network access | `network: bridge` + `allowed_hosts:` list |
| Writable mount | `volumes: [{host:..., container:..., ro: false}]` |
| Memory > 256MB | `memory: "512m"` (or unset for unlimited) |
| Different uid | `user: "1000:1000"` |

### YAML config

```yaml
upstream_servers:
  - name: filesystem
    command: ["uvx", "mcp-server-filesystem", "/data"]
    inherent_labels: ["confidential.personal"]
    isolation:
      image: docker.io/library/python:3.12-slim
      network: none
      volumes:
        - host: /home/me/notes
          container: /data
          ro: true
      memory: "256m"
      cpus: "0.5"
```

When the daemon starts an upstream session for this server,
`UpstreamServerConfig.effective_command` returns:

```
podman run --rm -i --read-only --cap-drop=ALL --security-opt=no-new-privileges
  --user=1500:1500 --network=none --memory=256m --cpus=0.5
  --volume=/home/me/notes:/data:ro
  docker.io/library/python:3.12-slim
  uvx mcp-server-filesystem /data
```

The MCP server runs inside the container; CapableDeputy talks to it
over stdio identically to a local subprocess.

### Quadlet for systemd-managed isolation

`quadlet_for(name, isolation, command)` emits a `.container` unit
encoding the same constraints declaratively:

```
[Container]
Image=docker.io/library/python:3.12-slim
User=1500:1500
ReadOnly=yes
DropCapability=ALL
NoNewPrivileges=yes
Network=none
Volume=/home/me/notes:/data:ro
Memory=256m
Exec=uvx mcp-server-filesystem /data
```

Drop the unit under `~/.config/containers/systemd/` and use systemctl
as usual. Audit tools like `systemd-analyze security` give the same
verdict whichever launch path you use.

### Threat model

What this prevents:

- Path traversal: a compromised filesystem server can't read
  outside its mount.
- SSRF: a compromised fetch server can't make outbound HTTP calls
  except to allowed hosts.
- Privilege escalation: `--cap-drop=ALL` + `no-new-privileges`
  leaves nothing to escalate to.

What this doesn't prevent:

- The upstream server returning malicious *content* — that's what
  `inherent_labels:` and the policy engine handle.
- Supply-chain compromise in the container image. Pin digests; review
  registries.

## Files

- `src/capabledeputy/upstream/isolation.py` — `ContainerIsolation`,
  `VolumeMount`, `quadlet_for`
- `src/capabledeputy/upstream/config.py` — YAML parsing,
  `effective_command`
- `src/capabledeputy/upstream/manager.py` — wraps upstream MCP launch
- `configs/upstream-isolated.example.yaml` — sample
- `docs/per-tool-isolation.md` — full operational guide
- `tests/test_e2e_isolation.py`
