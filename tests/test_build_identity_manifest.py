"""Snapshot evidence must retain dirty bytes without implying executable provenance."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "build_identity_recorder", Path(__file__).parents[1] / "scripts/record-build-identity.py"
)
assert _spec and _spec.loader
recorder = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(recorder)


def git(root: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *arguments], check=True, capture_output=True
    ).stdout


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    git(tmp_path, "init", "--quiet")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "Synthetic Test")
    source = tmp_path / "src/capabledeputy"
    source.mkdir(parents=True)
    (source / "core.py").write_text("answer = 1\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "--quiet", "-m", "fixture")
    return tmp_path


def manifest(root: Path, **kwargs):
    return recorder.build_manifest(
        root, configuration_label="isolated-synthetic", fixture_lane="accepted", **kwargs
    )


def test_daemon_hash_matches_runtime_algorithm_and_includes_new_modules(repository: Path):
    from capabledeputy.daemon.handlers import source_content_hash

    source = repository / "src/capabledeputy"
    (source / "untracked.py").write_text("second = 2\n")
    result = manifest(repository)
    assert result["components"]["daemon"]["source_content_hash"] == source_content_hash(source)
    assert [file["path"] for file in result["components"]["daemon"]["files"]] == [
        "core.py",
        "untracked.py",
    ]


def test_tracked_and_untracked_bytes_change_distinct_commitments(repository: Path):
    initial = manifest(repository)
    source = repository / "src/capabledeputy/core.py"
    source.write_text("answer = 2\n")
    dirty = manifest(repository)
    assert dirty["git"]["head"] == initial["git"]["head"]
    assert dirty["git"]["dirty_patch_sha256"] != initial["git"]["dirty_patch_sha256"]
    assert dirty["git"]["dirty"] is True
    git(repository, "add", ".")
    assert manifest(repository)["git"]["dirty_patch_sha256"] == dirty["git"]["dirty_patch_sha256"]
    untracked = repository / "new.py"
    untracked.write_text("new = 3\n")
    first = manifest(repository)
    untracked.write_text("new = 4\n")
    second = manifest(repository)
    assert first["git"]["dirty_patch_sha256"] == second["git"]["dirty_patch_sha256"]
    assert first["git"]["untracked_content_sha256"] != second["git"]["untracked_content_sha256"]


def test_binary_bytes_are_observed_without_source_or_running_provenance(repository: Path):
    binary = repository / "observed-app"
    binary.write_bytes(b"synthetic binary")
    result = manifest(repository, app_binary=binary)
    assert result["app_binary"] == {
        "status": "observed",
        "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
    }
    assert result["build_provenance"] == "unknown"
    assert result["running_process_provenance"] == "unknown"
    assert result["source_binary_relationship"] == "unverified"
    assert manifest(repository)["app_binary"]["status"] == "unknown"
    assert manifest(repository, app_binary=repository / "missing")["app_binary"]["sha256"] is None


def test_source_snapshot_excludes_native_build_cache_and_preserves_binary_fixtures(
    repository: Path,
):
    native = repository / "apps/macos/CapDep"
    (native / ".build").mkdir(parents=True)
    (native / "View.swift").write_text("struct View {}\n")
    (native / ".build/generated.swift").write_text("cached build\n")
    fixtures = repository / "tests/fixtures"
    fixtures.mkdir(parents=True)
    (fixtures / "image.png").write_bytes(b"synthetic image")
    result = manifest(repository)
    assert [entry["path"] for entry in result["components"]["native_app"]["files"]] == [
        "View.swift"
    ]
    assert result["components"]["fixtures"]["files"][0]["path"] == "image.png"


def test_private_content_and_locations_are_not_serialized(repository: Path):
    secret = "PRIVATE-ACCOUNT-CONTENT"
    (repository / "private-config.json").write_text(secret)
    result = json.dumps(manifest(repository))
    assert secret not in result
    assert "private-config.json" not in result
    assert str(repository) not in result


@pytest.mark.parametrize("label", ["account@example.com", "../private", "secret value", "x\n", ""])
def test_labels_reject_paths_addresses_and_multiline_values(repository: Path, label: str):
    with pytest.raises(ValueError):
        recorder.build_manifest(repository, configuration_label=label, fixture_lane="synthetic")


@pytest.mark.parametrize(
    "checks",
    [
        [{"command": ["ruff"], "exit_code": 0, "stdout": "private"}],
        [{"command": "ruff check", "exit_code": 0}],
        [{"command": ["ruff\nprivate"], "exit_code": 0}],
        [{"command": ["ruff"], "exit_code": True}],
    ],
)
def test_check_metadata_rejects_outputs_and_ambiguous_results(repository: Path, checks):
    with pytest.raises(ValueError):
        manifest(repository, checks=checks)


def test_checks_are_explicit_observations_and_git_absence_is_unknown(tmp_path: Path):
    checks = [{"command": [".venv/bin/ruff", "check"], "exit_code": 0}]
    result = manifest(tmp_path, checks=checks)
    assert result["checks"] == {"provenance": "caller_supplied_observations", "results": checks}
    assert result["git"]["status"] == "unknown"
    assert result["components"]["daemon"]["status"] == "unknown"


def test_source_symlink_cannot_import_external_content(repository: Path, tmp_path: Path):
    outside = tmp_path.parent / (tmp_path.name + "-outside.py")
    outside.write_text("PRIVATE-CONTENT")
    (repository / "src/capabledeputy/external.py").symlink_to(outside)
    with pytest.raises(ValueError, match="symbolic links"):
        manifest(repository)


def test_standalone_cli_uses_no_site_packages_and_preserves_existing_evidence(repository: Path):
    import sys

    output = repository / "identity.json"
    command = [
        sys.executable,
        "-S",
        str(Path(__file__).parents[1] / "scripts/record-build-identity.py"),
        "--repo-root",
        str(repository),
        "--configuration-label",
        "synthetic-isolated",
        "--fixture-lane",
        "unknown-restart",
        "--output",
        str(output),
    ]
    first = subprocess.run(command, capture_output=True, check=False)
    assert first.returncode == 0, first.stderr
    original = output.read_bytes()
    recorded = json.loads(original)
    assert recorded["app_binary"]["status"] == "unknown"
    assert recorded["configuration_label"] == "synthetic-isolated"
    second = subprocess.run(command, capture_output=True, check=False)
    assert second.returncode != 0
    assert output.read_bytes() == original


def test_component_directory_cannot_escape_repository(repository: Path, tmp_path: Path):
    outside = tmp_path.parent / (tmp_path.name + "-outside-fixtures")
    outside.mkdir()
    (outside / "private.py").write_text("private configuration")
    (repository / "tests").mkdir()
    (repository / "tests/fixtures").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="inside the repository"):
        manifest(repository)
