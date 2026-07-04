"""Reusable one-time setup domains for ``capdep-setup``.

These helpers deliberately keep mutating setup work behind explicit ``apply``
flags and injectable paths/runners so tests can exercise setup behavior without
touching the operator's real home directory, model cache, launchd state, or
repo-local image venv.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from capabledeputy.cli._managed_config import (
    GWORKSPACE_BLOCK_ID,
    GWORKSPACE_COMMUNITY_BLOCK_BODY,
    GWORKSPACE_DEFAULT_OFFICIAL_SERVICES,
    IMAP_BLOCK_BODY,
    IMAP_BLOCK_ID,
    google_workspace_official_block_body,
    register_default_assistant_surface,
    user_default_daemon_config_path,
    write_managed_block,
)

CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class SetupDomainResult:
    domain: str
    apply: bool
    status: str
    summary: str
    actions: tuple[str, ...] = ()
    commands: tuple[tuple[str, ...], ...] = ()
    changed: bool = False
    paths: dict[str, str] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "apply": self.apply,
            "status": self.status,
            "summary": self.summary,
            "actions": list(self.actions),
            "commands": [list(command) for command in self.commands],
            "changed": self.changed,
            "paths": self.paths,
            "details": self.details,
        }


def _default_runner(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=True,
        text=True,
        capture_output=True,
    )


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _path_map(**paths: Path | None) -> dict[str, str]:
    return {key: str(value) for key, value in paths.items() if value is not None}


def setup_assistant_surface(
    *,
    apply: bool = False,
    config_path: Path | None = None,
    no_sandbox: bool = False,
    force_sandbox: bool = False,
) -> SetupDomainResult:
    if no_sandbox and force_sandbox:
        raise ValueError("--no-sandbox and --force-sandbox are mutually exclusive")
    config_path = config_path or user_default_daemon_config_path()
    include_sandbox = False if no_sandbox else True if force_sandbox else None
    actions = (
        "register bundled fs/memory/git/fetch/search/image MCP server blocks",
        "register sandbox block if requested or if Podman is available",
    )
    if not apply:
        return SetupDomainResult(
            domain="assistant-surface",
            apply=False,
            status="dry_run",
            summary="Would register bundled assistant MCP servers in the daemon config.",
            actions=actions,
            paths=_path_map(daemon_config=config_path),
            details={"include_sandbox": include_sandbox},
        )
    messages = register_default_assistant_surface(config_path, include_sandbox=include_sandbox)
    return SetupDomainResult(
        domain="assistant-surface",
        apply=True,
        status="applied",
        summary="Registered bundled assistant MCP servers in the daemon config.",
        actions=tuple(messages),
        changed=True,
        paths=_path_map(daemon_config=config_path),
        details={"include_sandbox": include_sandbox},
    )


def setup_imap_register(
    *,
    apply: bool = False,
    config_path: Path | None = None,
) -> SetupDomainResult:
    config_path = config_path or user_default_daemon_config_path()
    actions = ("register IMAP MCP managed block",)
    if not apply:
        return SetupDomainResult(
            domain="imap",
            apply=False,
            status="dry_run",
            summary="Would register the IMAP MCP server block in the daemon config.",
            actions=actions,
            paths=_path_map(daemon_config=config_path),
        )
    replaced, changed = write_managed_block(config_path, IMAP_BLOCK_ID, IMAP_BLOCK_BODY)
    return SetupDomainResult(
        domain="imap",
        apply=True,
        status="applied",
        summary="Registered the IMAP MCP server block in the daemon config.",
        actions=(("refreshed" if replaced else "registered") + " IMAP block",),
        changed=changed,
        paths=_path_map(daemon_config=config_path),
    )


def setup_google_workspace_register(
    *,
    apply: bool = False,
    config_path: Path | None = None,
    mode: str = "official",
    services: str = "",
) -> SetupDomainResult:
    config_path = config_path or user_default_daemon_config_path()
    mode = mode.strip().lower()
    if mode not in {"official", "community"}:
        raise ValueError("--mode must be 'official' or 'community'")
    if mode == "official":
        services = services or GWORKSPACE_DEFAULT_OFFICIAL_SERVICES
        block_body = google_workspace_official_block_body(services)
        detail_services = tuple(s.strip() for s in services.split(",") if s.strip())
    else:
        services = services or "drive,sheets,calendar,docs,gmail"
        block_body = GWORKSPACE_COMMUNITY_BLOCK_BODY
        if services != "drive,sheets,calendar,docs,gmail":
            block_body = block_body.replace(
                '"--services", "drive,sheets,calendar,docs,gmail"',
                f'"--services", "{services}"',
            )
        detail_services = tuple(s.strip() for s in services.split(",") if s.strip())
    if not apply:
        return SetupDomainResult(
            domain="google-workspace",
            apply=False,
            status="dry_run",
            summary=f"Would register Google Workspace MCP config in {mode} mode.",
            actions=("register Google Workspace managed block",),
            paths=_path_map(daemon_config=config_path),
            details={"mode": mode, "services": detail_services},
        )
    replaced, changed = write_managed_block(config_path, GWORKSPACE_BLOCK_ID, block_body)
    return SetupDomainResult(
        domain="google-workspace",
        apply=True,
        status="applied",
        summary=f"Registered Google Workspace MCP config in {mode} mode.",
        actions=(("refreshed" if replaced else "registered") + " Google Workspace block",),
        changed=changed,
        paths=_path_map(daemon_config=config_path),
        details={"mode": mode, "services": detail_services},
    )


def image_setup_commands(repo_root: Path, venv_path: Path) -> tuple[tuple[str, ...], ...]:
    py = venv_path / "bin" / "python"
    packages = [
        "mflux>=0.18.0",
        "torch>=2.7.1",
        "diffusers>=0.30",
        "transformers>=5",
        "accelerate>=0.33",
        "safetensors>=0.4",
        "httpx>=0.28",
        "anyio>=4.4",
        "mcp>=1.0",
        "pyyaml>=6.0",
    ]
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        packages = [
            "torch>=2.2",
            "diffusers>=0.30",
            "transformers>=4.44,<5",
            "accelerate>=0.33",
            "safetensors>=0.4",
            "httpx>=0.28",
            "anyio>=4.4",
            "mcp>=1.0",
            "pyyaml>=6.0",
        ]
    return (
        (sys.executable, "-m", "venv", str(venv_path)),
        (str(py), "-m", "pip", "install", "-U", "pip", "wheel"),
        (str(py), "-m", "pip", "install", "-e", str(repo_root), "--no-deps"),
        (str(py), "-m", "pip", "install", *packages),
        (str(py), "-c", "import torch; print('torch', torch.__version__)"),
        (
            str(py),
            "-c",
            "from capabledeputy.mcp_servers import image_generate; "
            "print('tools', [t.name for t in image_generate.tools()])",
        ),
    )


def setup_images(
    *,
    apply: bool = False,
    repo_root: Path | None = None,
    venv_path: Path | None = None,
    command_runner: CommandRunner | None = None,
) -> SetupDomainResult:
    repo_root = repo_root or _default_repo_root()
    venv_path = venv_path or repo_root / ".venv-images"
    commands = image_setup_commands(repo_root, venv_path)
    if not apply:
        return SetupDomainResult(
            domain="images",
            apply=False,
            status="dry_run",
            summary="Would create/update the isolated image-generation Python environment.",
            commands=commands,
            paths=_path_map(repo_root=repo_root, image_venv=venv_path),
            details={"platform": platform.platform(), "machine": platform.machine()},
        )
    runner = command_runner or _default_runner
    for command in commands:
        venv_python = venv_path / "bin" / "python"
        if command[:3] == (sys.executable, "-m", "venv") and venv_python.is_file():
            continue
        runner(command)
    return SetupDomainResult(
        domain="images",
        apply=True,
        status="applied",
        summary="Created/updated the isolated image-generation Python environment.",
        commands=commands,
        changed=True,
        paths=_path_map(repo_root=repo_root, image_venv=venv_path),
    )


def setup_models(
    *,
    apply: bool = False,
    download: bool = False,
    cache_home: Path | None = None,
    command_runner: CommandRunner | None = None,
) -> SetupDomainResult:
    cache_home = cache_home or Path(
        os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface",
    )
    apple_silicon = platform.system() == "Darwin" and platform.machine() == "arm64"
    recommendations = [
        {
            "role": "planner",
            "profile": "balanced",
            "model": "mlx/Qwen/Qwen3-4B-MLX-4bit" if apple_silicon else "Qwen/Qwen3-4B",
        },
        {
            "role": "image",
            "profile": "fast",
            "model": "filipstrand/Z-Image-Turbo-mflux-4bit"
            if apple_silicon
            else "stabilityai/sdxl-turbo",
        },
    ]
    commands = tuple(
        ("hf", "download", str(item["model"]), "--cache-dir", str(cache_home))
        for item in recommendations
    )
    if download and not apply:
        raise ValueError("--download requires --apply")
    if apply and download:
        runner = command_runner or _default_runner
        for command in commands:
            runner(command)
    status = "downloaded" if apply and download else "ready_to_download" if apply else "dry_run"
    summary = (
        "Downloaded recommended local planner/image model assets."
        if apply and download
        else "Prepared a model harvesting plan; add --download to fetch assets."
        if apply
        else "Would inspect machine capability and recommend local planner/image models."
    )
    return SetupDomainResult(
        domain="models",
        apply=apply,
        status=status,
        summary=summary,
        actions=("inspect machine capability", "check Hugging Face token presence"),
        commands=commands,
        changed=bool(apply and download),
        paths=_path_map(hf_home=cache_home),
        details={
            "apple_silicon": apple_silicon,
            "hf_token_present": bool(
                os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN"),
            ),
            "recommendations": recommendations,
        },
    )


def setup_sandbox(
    *,
    apply: bool = False,
    command_runner: CommandRunner | None = None,
) -> SetupDomainResult:
    podman = shutil.which("podman")
    health: dict[str, Any] = {"checked": False}
    if apply and podman:
        runner = command_runner or _default_runner
        version = runner((podman, "--version"))
        info = runner((podman, "info", "--format", "json"))
        health = {
            "checked": True,
            "version": (version.stdout or "").strip(),
            "info_present": bool((info.stdout or "").strip()),
        }
    return SetupDomainResult(
        domain="sandbox",
        apply=apply,
        status="ready" if podman else "missing_podman",
        summary=(
            "Podman is available for sandbox execution."
            if podman
            else "Podman is not on PATH; sandbox setup would need Podman installed first."
        ),
        actions=("check Podman availability", "verify sandbox runtime health"),
        commands=((podman, "--version"), (podman, "info", "--format", "json")) if podman else (),
        paths=_path_map(podman=Path(podman) if podman else None),
        details={"runtime_health": health},
    )


def setup_macos_daemon(
    *,
    apply: bool = False,
    repo_root: Path | None = None,
    verify: bool = False,
    command_runner: CommandRunner | None = None,
) -> SetupDomainResult:
    repo_root = repo_root or _default_repo_root()
    launchd_script = repo_root / "scripts" / "run-local-daemon-launchd.sh"
    tmux_script = repo_root / "scripts" / "run-local-daemon-tmux.sh"
    parity_script = repo_root / "scripts" / "verify-gui-parity.py"
    commands: tuple[tuple[str, ...], ...] = (
        ("launchctl", "list"),
        ("ps", "-axo", "pid,command"),
    )
    parity_command = (sys.executable, str(parity_script))
    if verify:
        commands = (*commands, parity_command)
    if verify and not apply:
        raise ValueError("--verify requires --apply")
    checks: list[dict[str, Any]] = []
    if apply:
        runner = command_runner or _default_runner
        for command in commands:
            completed = runner(command)
            checks.append(
                {
                    "command": list(command),
                    "returncode": completed.returncode,
                    "stdout_present": bool(completed.stdout),
                },
            )
    return SetupDomainResult(
        domain="macos-daemon",
        apply=apply,
        status="verified" if apply and verify else "checked" if apply else "dry_run",
        summary=(
            "Inspected daemon launch paths and parity prerequisites."
            if apply
            else "Would inspect daemon launch paths and run connectivity/parity checks; "
            "real launchd changes stay opt-in."
        ),
        actions=(
            "validate launchd daemon script",
            "validate tmux daemon script",
            "run daemon connectivity/parity check after explicit launch",
        ),
        commands=commands,
        paths=_path_map(
            launchd_script=launchd_script,
            tmux_script=tmux_script,
            parity_script=parity_script,
        ),
        details={
            "launchd_script_present": launchd_script.is_file(),
            "tmux_script_present": tmux_script.is_file(),
            "parity_script_present": parity_script.is_file(),
            "checks": checks,
        },
    )


def result_to_json(result: SetupDomainResult) -> str:
    return json.dumps(result.as_dict(), indent=2)
