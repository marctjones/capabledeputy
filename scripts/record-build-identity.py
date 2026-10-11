#!/usr/bin/env python3
"""Record current source and explicitly supplied binary bytes, never build provenance.

Configuration and fixture labels are operator-supplied identifiers. No private
configuration, daemon, account, or model is inspected. Optional checks JSON must
contain only public command arguments and observed exit codes, never outputs.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")


def safe_label(value: str) -> str:
    if not LABEL.fullmatch(value):
        raise ValueError(
            "Identifiers must be 1-64 ASCII letters, digits, dots, underscores or hyphens"
        )
    return value


def content_manifest(root: Path, pattern: str, *, exclude_build: bool = False) -> dict[str, Any]:
    """Use the daemon's path/content length framing; include untracked source."""
    digest = hashlib.sha256()
    files = []
    if not root.is_dir():
        return {"status": "unknown", "source_content_hash": None, "files": []}
    for path in sorted(root.rglob(pattern)):
        if exclude_build and ".build" in path.relative_to(root).parts:
            continue
        if path.is_dir():
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Source snapshots cannot follow symbolic links")
        name = path.relative_to(root).as_posix().encode()
        data = path.read_bytes()
        digest.update(len(name).to_bytes(8, "big") + name)
        digest.update(len(data).to_bytes(8, "big") + data)
        files.append({"path": name.decode(), "sha256": hashlib.sha256(data).hexdigest()})
    return {"status": "observed", "source_content_hash": digest.hexdigest(), "files": files}


def git_output(root: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        timeout=30,
    ).stdout


def git_manifest(root: Path) -> dict[str, Any]:
    # Never publish patch contents or git error messages: either may be private.
    try:
        head = git_output(root, "rev-parse", "HEAD").decode().strip()
        patch = git_output(root, "diff", "--binary", "--no-ext-diff", "--no-textconv", "HEAD")
        untracked = git_output(root, "ls-files", "--others", "--exclude-standard", "-z")
        digest = hashlib.sha256()
        count = 0
        for name in sorted(name for name in untracked.split(b"\0") if name):
            path = root / name.decode()
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                raise ValueError("Untracked snapshots cannot follow symbolic links")
            data = path.read_bytes()
            digest.update(len(name).to_bytes(8, "big") + name)
            digest.update(len(data).to_bytes(8, "big") + data)
            count += 1
        return {
            "status": "observed",
            "head": head,
            "dirty": bool(patch or count),
            "dirty_patch_sha256": hashlib.sha256(patch).hexdigest(),
            "dirty_patch_scope": "tracked staged and unstaged changes against HEAD",
            "untracked_content_sha256": digest.hexdigest(),
            "untracked_file_count": count,
        }
    except (OSError, UnicodeError, subprocess.SubprocessError):
        return {"status": "unknown", "head": None, "dirty_patch_sha256": None}


def check_metadata(value: Any) -> list[dict[str, Any]]:
    """Accept explicit command/exit observations, excluding captured output."""
    if not isinstance(value, list):
        raise ValueError("Checks must be a list")
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != {"command", "exit_code"}:
            raise ValueError("Checks contain only command and exit_code")
        command = entry["command"]
        if (
            not isinstance(command, list)
            or not command
            or not all(
                isinstance(arg, str) and arg and not any(c in arg for c in "\r\n\0")
                for arg in command
            )
        ):
            raise ValueError("Each command must contain nonempty single-line arguments")
        if type(entry["exit_code"]) is not int:
            raise ValueError("Observed exit_code must be an integer")
    return value


def build_manifest(
    root: Path,
    *,
    app_binary: Path | None = None,
    configuration_label: str,
    fixture_lane: str,
    checks: Any = None,
) -> dict[str, Any]:
    configuration_label = safe_label(configuration_label)
    fixture_lane = safe_label(fixture_lane)
    observed_checks = check_metadata(checks if checks is not None else [])
    binary: dict[str, Any] = {"status": "unknown", "sha256": None}
    if app_binary is not None:
        with contextlib.suppress(OSError):
            binary = {
                "status": "observed",
                "sha256": hashlib.sha256(app_binary.read_bytes()).hexdigest(),
            }
    components = {}
    for name, relative, pattern in (
        ("daemon", "src/capabledeputy", "*.py"),
        ("native_app", "apps/macos/CapDep", "*.swift"),
        ("fixtures", "tests/fixtures", "*"),
    ):
        component_root = root / relative
        if not component_root.resolve().is_relative_to(root.resolve()):
            raise ValueError("Source components must remain inside the repository")
        components[name] = {
            "root": relative,
            **content_manifest(component_root, pattern, exclude_build=name == "native_app"),
        }
    return {
        "schema_version": 1,
        "evidence_kind": "current_checkout_snapshot_and_supplied_binary_bytes",
        "build_provenance": "unknown",
        "running_process_provenance": "unknown",
        "source_binary_relationship": "unverified",
        "git": git_manifest(root),
        "components": components,
        "app_binary": binary,
        "configuration_label": configuration_label,
        "fixture_lane": fixture_lane,
        "checks": {"provenance": "caller_supplied_observations", "results": observed_checks},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--app-binary", type=Path)
    parser.add_argument("--configuration-label", required=True)
    parser.add_argument("--fixture-lane", required=True)
    parser.add_argument(
        "--checks-json", type=Path, help="Public command/exit-code observations only"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        checks = json.loads(args.checks_json.read_text()) if args.checks_json else []
        manifest = build_manifest(
            args.repo_root.resolve(),
            app_binary=args.app_binary,
            configuration_label=args.configuration_label,
            fixture_lane=args.fixture_lane,
            checks=checks,
        )
    except (ValueError, OSError):
        parser.error("Invalid manifest input; use safe labels and public command/exit metadata")
    # Exclusive creation prevents replacing evidence from a preceding run.
    with args.output.open("x") as output:
        output.write(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
