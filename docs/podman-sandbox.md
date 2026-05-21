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
| **In: stdin** | `stdin_bytes` argument to `execute()` (`stdin` in the `sandbox.run` tool). Piped to the container's stdin before output capture starts. |
| **In: files (per-execution)** | `inputs={name: bytes}` argument. Each name lands at `/in/<name>` inside the container as a read-only file. `auto_io_mounts=True` (default) is required. |
| **In: persistent file tree** | Read-only bind mount declared in the region spec under `mounts:`. |
| **Out: stdout / stderr** | Captured by the actuator; hashed into `SandboxResult.output_digest`. Streamed live via `progress_callback`. |
| **Out: files (per-execution)** | The container writes to `/out/<name>`. Each file is harvested into `SandboxResult.outputs` (name, size, sha256, utf-8 preview, truncated flag). Full bytes available via `actuator.read_output(region_id, name)` until the region is discarded. |
| **Out: persistent file tree** | Operator-declared write-allowed bind mount (`read_only: false`) — the host-side dir is the operator's responsibility to scope and clean up. |
| **Network** | Default `none`. Override per-region via `network: bridge` or `network: slirp4netns:...`; do this only with an explicit egress allowlist policy in mind. |

### Per-execution auto-IO (the common path)

Every region with `auto_io_mounts: true` (default) gets fresh
`/in` (read-only) and `/out` (read-write) mounts on every `execute()`
call. The host-side dirs live in a per-region temp dir that is
cleaned up on `discard_region()` — so each run is truly disposable,
even when files were exchanged.

Mount options used: `:Z,U` — `:Z` is the SELinux relabel (mandatory
on Fedora/RHEL, harmless elsewhere); `:U` chowns the mount to the
container's effective uid (65534/nobody) so the unprivileged user can
actually read /in and write /out under rootless Podman. Without `:U`
the container exits with permission denied.

### Caps on output harvest

| Field | Default | Effect |
|-------|---------|--------|
| `output_preview_bytes` | 4096 | Bytes returned inline in the result's `preview` field. Larger files set `truncated=true` and require `read_output()` for the full bytes. |
| `output_max_files` | 32 | Cap on number of files harvested. Files beyond this limit are silently dropped. |
| `output_max_total_bytes` | 16 MiB | Total bytes across all outputs. The harvest stops once this is reached. |

These caps protect the daemon from a runaway container that fills
`/out` with gigabytes of data.

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
outputs.

The agent calls `sandbox.run`:

```jsonc
{
  "spec_id": "scratch",
  "argv": ["/bin/sh", "-c", "tr 'a-z' 'A-Z' < /in/text.txt > /out/result.txt"],
  "inputs": {"text.txt": "hello world"},
  "timeout_seconds": 30
}
```

Result:

```jsonc
{
  "spec_id": "scratch",
  "exit_code": 0,
  "output_digest": "...",
  "cancelled": false,
  "timed_out": false,
  "outputs": [
    {
      "name": "result.txt",
      "size": 12,
      "sha256": "...",
      "preview": "HELLO WORLD\n",
      "truncated": false
    }
  ]
}
```

The chokepoint gates the call:
1. Session must hold `EXECUTE_SANDBOX <spec_id>` capability — grant via
   `/grant EXECUTE_SANDBOX <spec_id>` (or `/grant EXECUTE_SANDBOX *`).
2. The `EXECUTE.sandbox` effect_class fails-closed if no actuator is
   wired (FR-042 / SC-017). When wired, the standard v2 pipeline runs.
3. After dispatch, `compose_with_isolation` lifts effective
   reversibility to `reversible/system` because the run happened
   inside a disposable region (FR-040). Outputs that leave the region
   retain their source labels (FR-041).

## Testing

```bash
# Unit tests (mocked subprocess, no podman required)
pytest tests/test_podman_sandbox.py

# Integration test (runs against real podman if available)
pytest tests/test_podman_sandbox.py::test_real_podman_smoke
```
