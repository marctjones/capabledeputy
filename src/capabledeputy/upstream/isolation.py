"""Per-tool container isolation for upstream MCP servers (DESIGN.md §15, v0.4).

Each upstream MCP server can be wrapped in its own podman/docker
container with policy-driven network and filesystem views. The
isolation profile is declared in the YAML config:

    upstream_servers:
      - name: filesystem
        command: ["uvx", "mcp-server-filesystem", "/data"]
        isolation:
          image: docker.io/library/python:3.14-slim
          network: none           # or 'bridge' + allowed_hosts list
          volumes:
            - host: /home/marc/Documents
              container: /data
              ro: true
          memory: "256m"
          cpus: "0.5"

The manager rewrites the launch command to invoke podman with the
profile applied; the upstream server still talks stdio so MCP transport
is unchanged. The `LabeledMcpAdapter` doesn't even know its server is
containerized — that's the point.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

NetworkMode = Literal["none", "bridge", "host"]


@dataclass(frozen=True)
class VolumeMount:
    host: str
    container: str
    ro: bool = True


@dataclass(frozen=True)
class ContainerIsolation:
    """Container profile for an upstream MCP server.

    Defaults are deliberately strict:
      - `network='none'` — no networking unless explicitly opened.
      - read-only root filesystem.
      - read-only volume binds unless ro=False.
      - no privileged flags.

    Hardening features common to podman:
      - `--cap-drop=ALL` to drop Linux capabilities.
      - `--security-opt=no-new-privileges` to block privilege escalation.
      - `--user 1500:1500` to match the daemon's rootless uid.
    """

    image: str
    network: NetworkMode = "none"
    allowed_hosts: tuple[str, ...] = ()
    volumes: tuple[VolumeMount, ...] = ()
    memory: str | None = None
    cpus: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    user: str = "1500:1500"
    runtime: Literal["podman", "docker"] = "podman"

    def __post_init__(self) -> None:
        if self.network == "bridge" and not self.allowed_hosts:
            raise ValueError(
                "isolation.network='bridge' requires a non-empty allowed_hosts list",
            )
        if self.network == "host":
            raise ValueError(
                "isolation.network='host' bypasses the per-upstream allowlist; "
                "use 'none' or 'bridge' with allowed_hosts",
            )

    def to_argv_prefix(self) -> tuple[str, ...]:
        """Build the `podman run` (or `docker run`) argv prefix that
        wraps the upstream server command. The caller appends the
        original command argv, in order:

            <prefix...>  IMAGE  <upstream-cmd...>
        """
        argv: list[str] = [
            self.runtime,
            "run",
            "--rm",
            "-i",  # stdin attached for stdio MCP
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            f"--user={self.user}",
            f"--network={self.network}",
        ]
        if self.network == "bridge":
            # Disable container DNS resolution so only operator-pinned
            # --add-host names are resolvable inside the upstream.
            argv.append("--dns=none")
            for host in self.allowed_hosts:
                argv.append(f"--add-host={host}")
        if self.memory:
            argv.append(f"--memory={self.memory}")
        if self.cpus:
            argv.append(f"--cpus={self.cpus}")
        for v in self.volumes:
            suffix = ":ro" if v.ro else ""
            argv.append(f"--volume={v.host}:{v.container}{suffix}")
        for k, val in sorted(self.env.items()):
            argv.append(f"--env={k}={val}")
        argv.append(self.image)
        return tuple(argv)


def quadlet_for(name: str, isolation: ContainerIsolation, command: tuple[str, ...]) -> str:
    """Generate a systemd quadlet (`.container`) file for one upstream
    server. Drop the result under `~/.config/containers/systemd/` and
    `systemctl --user daemon-reload && systemctl --user start
    capdep-upstream-<name>` will run the server isolated.

    The unit description encodes everything the runtime would have
    produced via `to_argv_prefix`, but in declarative quadlet form so
    users can audit it with their existing systemd tooling.
    """
    lines = [
        "[Unit]",
        f"Description=CapableDeputy upstream MCP server: {name}",
        "After=network-online.target",
        "",
        "[Container]",
        f"Image={isolation.image}",
        f"User={isolation.user}",
        "ReadOnly=yes",
        "DropCapability=ALL",
        "NoNewPrivileges=yes",
        f"Network={isolation.network}",
    ]
    if isolation.network == "bridge":
        for host in isolation.allowed_hosts:
            lines.append(f"AddHost={host}")
    if isolation.memory:
        lines.append(f"Memory={isolation.memory}")
    if isolation.cpus:
        lines.append(f"PodmanArgs=--cpus={isolation.cpus}")
    for v in isolation.volumes:
        ro = ":ro" if v.ro else ""
        lines.append(f"Volume={v.host}:{v.container}{ro}")
    for k, val in sorted(isolation.env.items()):
        lines.append(f"Environment={k}={val}")
    lines.append("Exec=" + " ".join(command))
    lines.extend(["", "[Install]", "WantedBy=default.target", ""])
    return "\n".join(lines)
