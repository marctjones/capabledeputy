# Per-tool container isolation

Beyond v0.2's all-in-one container, every upstream MCP server can run
in its own podman container with policy-driven networking and
filesystem views. This is the strongest blast-radius containment
CapableDeputy offers: a compromised filesystem MCP server cannot reach
the network; a compromised fetch MCP server cannot read your home
directory.

## Configuration

Each entry in `upstream_servers:` accepts an `isolation:` block:

```yaml
upstream_servers:
  - name: filesystem
    command: ["uvx", "mcp-server-filesystem", "/data"]
    inherent_labels: ["confidential.personal"]
    isolation:
      image: docker.io/library/python:3.12-slim
      network: none                 # 'none' | 'bridge' | 'host'
      allowed_hosts: []              # only meaningful with 'bridge'
      volumes:
        - host: /home/me/Documents
          container: /data
          ro: true                   # default
      memory: "256m"
      cpus: "0.5"
      user: "1500:1500"              # rootless uid by default
      runtime: "podman"              # or 'docker'

  - name: fetch
    command: ["uvx", "mcp-server-fetch"]
    inherent_labels: ["untrusted.external"]
    isolation:
      image: docker.io/library/python:3.12-slim
      network: bridge
      allowed_hosts:
        - "api.openai.com:140.82.121.4"
      memory: "128m"
```

When `isolation:` is set, the runtime wraps the upstream command in:

```
podman run --rm -i --read-only --cap-drop=ALL \
           --security-opt=no-new-privileges \
           --user=1500:1500 \
           --network=<mode> [--add-host ...] \
           --memory=... --cpus=... \
           --volume=...:...[:ro] \
           IMAGE  <upstream cmd...>
```

MCP transport remains stdio; `LabeledMcpAdapter` is unchanged.

## Strict defaults

The `ContainerIsolation` value class biases hard toward least
privilege:

- `network=none` — no networking unless you opt in.
- `--read-only` root filesystem; volumes default to `ro=true`.
- `--cap-drop=ALL` — every Linux capability dropped.
- `--security-opt=no-new-privileges` — `setuid` cannot escalate.
- `user=1500:1500` — same rootless uid the daemon container uses.

You can relax any of these per server, but the defaults are what
ships. Adding a deliberate `ro: false` is a flag review of your YAML
config — easy to spot in `git diff`.

## Network policy

| Mode | Use when |
|---|---|
| `none` | Filesystem servers, calculation servers, anything that just reads from a mount and returns text. Most MCP servers. |
| `bridge` + `allowed_hosts` | Web fetch / API access. Pin specific hosts so a compromised server can't talk to arbitrary endpoints. |
| `host` | Last resort — the container shares the host network namespace. Use only when bridging is impractical. |

`allowed_hosts` is rendered as `--add-host` so DNS resolves only the
allowed entries to the IPs you specified; combined with default-deny
egress on the bridge, this gives you a host allowlist without a full
firewall ruleset.

## systemd quadlet output

For users who want systemd-managed isolation rather than the
runtime's per-process podman exec, `capabledeputy.upstream.isolation
.quadlet_for` generates a `.container` quadlet file per server. Drop
it under `~/.config/containers/systemd/`, run `systemctl --user
daemon-reload`, and start with `systemctl --user start
capdep-upstream-<name>`.

The quadlet encodes the same constraints (`ReadOnly=yes`,
`DropCapability=ALL`, `NoNewPrivileges=yes`, etc.) so audit tools
like `systemd-analyze security` give the same verdict whichever
launch path you use.

## Threat model

What this prevents:

- A compromised upstream server can't read your home directory if
  you didn't mount it. ([CWE-22 / path traversal across the runtime
  boundary.](https://cwe.mitre.org/data/definitions/22.html))
- A compromised upstream server can't make outbound HTTP calls if
  `network=none`. ([CWE-918 / SSRF blocked at the kernel.](https://cwe.mitre.org/data/definitions/918.html))
- A privilege escalation inside the container can't break out
  because `--user=1500:1500` and `--cap-drop=ALL` leave nothing to
  escalate to.

What this doesn't prevent:

- The upstream server returning malicious *content* — that's what
  `inherent_labels: untrusted.external` and the rest of the policy
  engine are for.
- Supply-chain compromise in the container image itself. Pin
  digests; review your image registries.

Container isolation is the bottom layer of defence-in-depth, not a
substitute for the labels-and-capabilities model.
