# Container Deployment (v0.2)

CapableDeputy ships with a Containerfile and Podman quadlet so you
can run the daemon as a rootless container with locked-down
networking and explicit volume mounts.

## Why containerize

The IFC/capability work in v0.1 stops the LLM from misusing tools
*it was given access to*. The container layer adds defense against
the daemon process itself being exploited (path traversal, RCE in a
dependency, etc.) by:

- **Default-deny network egress** — only Anthropic API endpoints
  reachable; no accidental SSRF, no credential exfil to attacker-
  controlled hosts.
- **Filesystem confinement** — daemon sees only `/var/lib/capabledeputy`
  for state and explicitly mounted user-data dirs.
- **Process isolation** — no /proc spelunking from inside the
  container; no signaling host processes.
- **Reproducibility** — same image, same behavior, anywhere.

This is **defense in depth**. The structural label/capability
guarantees in v0.1 already hold without containers. Containerizing
adds belt-and-suspenders coverage for hypothetical bugs in the
daemon itself.

## Quick start with Podman

```bash
podman build -t capabledeputy:0.1 .

mkdir -p ~/.local/share/capabledeputy ~/.run/capdep

podman run --rm -it \
  --name capdep \
  -e ANTHROPIC_API_KEY \
  -v ~/.local/share/capabledeputy:/var/lib/capabledeputy:Z \
  -v ~/.run/capdep:/run/capdep:Z \
  -e CAPDEP_DATA_DIR=/var/lib/capabledeputy \
  -e CAPDEP_SOCKET=/run/capdep/capdep.sock \
  capabledeputy:0.1
```

On the host, talk to the daemon over the bind-mounted socket:

```bash
export CAPDEP_SOCKET=$HOME/.run/capdep/capdep.sock
uv run capdep session list
uv run capdep tui
```

## systemd quadlet (managed daemon)

For a daily-use install:

1. Copy `deploy/capabledeputy.container` to `~/.config/containers/systemd/`.
2. Set up a host env file at `~/.config/capabledeputy/env` containing:
   ```
   ANTHROPIC_API_KEY=sk-ant-...
   ```
3. Reload and start:
   ```bash
   systemctl --user daemon-reload
   systemctl --user start capabledeputy.service
   ```

The daemon now starts on login, restarts on failure, and is
rootless. State and socket bind-mount to your home directory so
terminal clients work as before.

## Default-deny networking

The simplest version is a plain Podman network with `--network none`
plus a host firewall rule allowing only Anthropic. For production:

1. Define `capabledeputy.network` (CNI/netavark) with explicit egress
   rules.
2. Reference it in the quadlet via `Network=capabledeputy.network`.

Detailed network configuration is deployment-specific and beyond
the scope of this guide; the container image itself does not assume
internet access beyond what it needs for LLM API calls.

## Volume layout

| Host path | Container path | What's there |
|---|---|---|
| `~/.local/share/capabledeputy/` | `/var/lib/capabledeputy/` | `state.db`, `audit.jsonl` |
| `~/.run/capdep/` | `/run/capdep/` | `capdep.sock` (Unix socket) |
| (optional) `~/health/` | `/data/health/` (read-only) | User health docs |
| (optional) `~/finance/` | `/data/finance/` (read-only) | Financial docs |

Mount user-data directories selectively. The capability/label model
applies *inside* the container regardless of what's mounted, but
mounting only what's needed keeps the OS-level blast radius small.

## Path overrides

The daemon reads:

| Env var | Default | Container default |
|---|---|---|
| `CAPDEP_SOCKET` | `$XDG_RUNTIME_DIR/capdep.sock` | `/run/capdep/capdep.sock` |
| `CAPDEP_STATE_DB` | `$CAPDEP_DATA_DIR/state.db` | `/var/lib/capabledeputy/state.db` |
| `CAPDEP_AUDIT_LOG` | `$CAPDEP_DATA_DIR/audit.jsonl` | `/var/lib/capabledeputy/audit.jsonl` |
| `CAPDEP_DATA_DIR` | `$XDG_DATA_HOME/capabledeputy` | `/var/lib/capabledeputy` |
| `CAPDEP_LLM_MODEL` | `claude-haiku-4-5` | `claude-haiku-4-5` |

All env-driven so the container deployment is configuration, not
recompilation.

## Container security posture

The image:
- Runs as **uid 1500 (capdep)**, not root.
- Uses **python:3.12-slim** as base — small attack surface.
- Two-stage build separates `uv sync` from runtime so dev tools
  aren't shipped.
- **No shell or interactive tools** in the runtime image (intentional).
- ENTRYPOINT is `capdep`; CMD is `daemon start`. Override CMD to run
  one-shot subcommands like `capdep version`.

## Container CI

The intended CI lane runs the test suite inside the container:

```bash
podman run --rm capabledeputy:0.1 sh -c 'cd /opt/capabledeputy && uv run pytest -q'
```

This catches drift between local and container behavior.
