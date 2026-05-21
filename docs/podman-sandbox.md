# Podman SandboxActuator

Rootless Podman as a `SandboxActuator` provider for `EXECUTE.sandbox`
effects. Each disposable region maps to a one-shot, ephemeral
container; the container's lifetime is the unit of containment.

## Why this exists

The policy engine refuses `EXECUTE.sandbox` effects unless a
`SandboxActuator` is wired — this is the Constitution Principle VI
fail-closed behavior on FR-042. Without an actuator, the engine
cannot promise containment, so it returns `OVERRIDE_REQUIRED`. The
Podman provider is the first production-shaped actuator (the
`in_process` actuator is a demo stub with no real isolation).

When a sandbox provider is wired:
- Effective reversibility lifts to `reversible/system` while a run
  executes inside a region (FR-040): tearing down the region undoes
  every side effect inside it, by construction.
- Outputs that leave the region retain their source category
  labels (FR-041) — containment kills the side effect, not the
  classification.

## Install

Rootless Podman, per your distro:

```bash
# Debian / Ubuntu
sudo apt install podman

# Fedora / RHEL
sudo dnf install podman

# Arch
sudo pacman -S podman
```

Verify with `podman --version`. No daemon needed — Podman is fork-and-
exec, which is exactly what the actuator drives.

## Enable in daemon config

Add a `sandbox:` block to your daemon config (the user-local default
is `~/.config/capabledeputy/daemon.yaml`):

```yaml
sandbox:
  provider: podman
  regions:
    - id: scratch-python
      image: docker.io/library/python:3.12-slim
      network: none           # no egress unless overridden
      memory_mb: 512
      cpus: 1.0
      pids_limit: 128
      timeout_seconds_default: 30
      require_digest_pin: false  # set true in production
      env_allowlist:
        - PYTHONUNBUFFERED
      mounts:
        - host: /var/cache/capdep/scratch
          container: /work
          read_only: false        # write-out mount
```

Restart the daemon. Startup log will report:

```
[sandbox] PodmanSandboxActuator wired with 1 region spec(s): scratch-python
```

## Defaults that are always applied

Every `podman run` invocation includes:

| Flag | Why |
|------|-----|
| `--rm` | Auto-remove on exit; no zombie containers. |
| `--read-only` | Read-only rootfs. |
| `--cap-drop=ALL` | Drop every Linux capability. |
| `--security-opt no-new-privileges` | Block setuid escalation. |
| `--user 65534:65534` | Run as `nobody`. |
| `--network <region.network>` | Default `none` — no egress. |
| `--memory <region.memory_mb>m` | RAM cap. |
| `--cpus <region.cpus>` | CPU cap. |
| `--pids-limit <region.pids_limit>` | Fork-bomb cap. |
| `--name capdep-<region_id>` | Stable name so cancel/discard finds it. |

## Production hardening checklist

- [ ] `require_digest_pin: true` and reference images by `name@sha256:...`
      — prevents image-tag-poisoning.
- [ ] `network: none` unless a region genuinely needs egress.
- [ ] `mounts` only as needed; prefer `read_only: true` and only allow
      writes on dedicated output paths.
- [ ] Pin Podman version in your distro's package manager — the
      `--cap-drop=ALL` and `--security-opt` semantics are stable, but
      regressions happen.
- [ ] Drop the `in_process` provider entirely from production
      configs: it's a demo stub with no isolation.

## Data in / out

| Direction | Mechanism |
|-----------|-----------|
| **In: small payload** | `stdin_bytes` argument to `execute()` — piped to the container's stdin before output capture starts. |
| **In: file tree** | Read-only bind mount declared in the region spec (`read_only: true`). |
| **Out: stdout / stderr** | Captured by the actuator; hashed into `SandboxResult.output_digest`. Streamed live via `progress_callback`. |
| **Out: file tree** | Write-allowed bind mount (`read_only: false`); the host-side directory is your responsibility to scope and clean up. |
| **Network** | Default `none`. Override per-region via `network: bridge` or `network: slirp4netns:...`; do this only with an explicit egress allowlist policy in mind. |

## Progress + cancellation

`execute()` accepts an optional `progress_callback` which fires for:

- Lifecycle events (`image_check`, `container_start`,
  `container_exit:<rc>`, `cancelled`, `timed_out`).
- Each line-buffered chunk of stdout and stderr.

A separate thread or coroutine can call `actuator.cancel(region_id)`
to send a `podman kill` to a running container. The current
`execute()` call then returns with `cancelled=True` and the actual
container exit code (typically 137).

`discard_region(region_id)` is the harder kill: it removes the
region from the actuator's state and force-kills any running
container. This is what FR-040 calls the "region death = containment
guarantee" path.

## Operational gotchas

- **First run pulls images.** `podman run` will fetch unknown images
  on first use; this can be slow and is not progress-reported beyond
  the `image_check` lifecycle event. Pre-pull in production:
  `podman pull docker.io/library/python:3.12-slim`.
- **SELinux relabel (`:Z`).** All bind mounts use `:Z` so they work
  on SELinux-enforcing distros (Fedora/RHEL). Harmless on Ubuntu/Debian.
- **Rootless filesystem quirks.** The container runs as
  `65534:65534` (nobody). Host directories you mount must be readable
  (and writable, for non-RO mounts) by that uid. Typically: `chmod o+rwX`
  or use a dedicated host-side ownership scheme.
- **No daemon mode.** The actuator drives `podman` via subprocess
  per execute. No persistent podman service is required.

## What the agent sees

When an actuator is wired, the LLM system prompt includes a Sandbox
section listing region ids, images, and network status. The agent is
told that containment lifts reversibility and does **not** declassify
outputs. The agent currently cannot start a sandbox run directly —
that's a forthcoming `sandbox.exec` tool. Today the operator drives
runs through the substrate API.

## Testing

```bash
# Unit tests (mocked subprocess, no podman required)
pytest tests/test_podman_sandbox.py

# Integration test (runs against real podman if available)
pytest tests/test_podman_sandbox.py::test_real_podman_smoke
```
