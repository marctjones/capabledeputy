from __future__ import annotations

import json

import pytest

from capabledeputy.approval.signer import SoftwareKeySigner
from capabledeputy.artifacts import (
    ArtifactEffect,
    ArtifactType,
    artifact_approval_payload,
)
from capabledeputy.policy.tiers import Tier
from capabledeputy.substrate.script_workspace import (
    ScriptWorkspaceError,
    ScriptWorkspaceSourcePort,
    make_file_export_artifact,
    make_script_artifact,
    make_script_run_artifact,
)
from capabledeputy.substrate.source_port import get_source_port


def test_script_workspace_canonicalizes_paths_and_rejects_escapes(tmp_path) -> None:
    workspace = ScriptWorkspaceSourcePort(tmp_path, workspace_id="photos")
    (tmp_path / "input.txt").write_text("hello", encoding="utf-8")

    assert workspace.canonicalize_resource("input.txt") == "script-workspace:photos:input.txt"
    assert workspace.identity_for_file("input.txt").sha256

    with pytest.raises(ScriptWorkspaceError):
        workspace.canonicalize_resource("../outside.txt")


def test_source_port_registry_exposes_script_workspace(tmp_path) -> None:
    port = get_source_port("safe-scripting", root=tmp_path, workspace_id="batch")

    assert (
        port.canonical_destination_id("out/result.txt")
        == "script-workspace:batch:out/result.txt"
    )


def test_secret_like_workspace_files_are_labeled_restricted_credentials(tmp_path) -> None:
    (tmp_path / ".env").write_text("TOKEN=abc", encoding="utf-8")
    workspace = ScriptWorkspaceSourcePort(tmp_path)

    labels = workspace.identity_for_file(".env").labels
    credential = next(tag for tag in labels.a if tag.category == "credentials")

    assert credential.tier is Tier.RESTRICTED


def test_script_artifact_approval_binds_exact_code_and_destination(tmp_path) -> None:
    workspace = ScriptWorkspaceSourcePort(tmp_path, workspace_id="scripts")
    artifact = make_script_artifact(
        title="Rename photos",
        code="print('rename')\n",
        language="python",
        workspace=workspace,
        target_path="rename_photos.py",
    )

    assert artifact.artifact_type is ArtifactType.SCRIPT
    assert artifact.effect is ArtifactEffect.CREATE
    assert artifact.destination_id == "script-workspace:scripts:rename_photos.py"
    assert artifact.metadata["language"] == "python"

    message = artifact_approval_payload(
        approval_id=40,
        action="CREATE_SCRIPT",
        artifact=artifact,
        labels_in=[],
    )
    signature = SoftwareKeySigner(key=b"x" * 32, key_id="sw:test").sign(message)
    tampered = make_script_artifact(
        title="Rename photos",
        code="print('delete')\n",
        language="python",
        workspace=workspace,
        target_path="rename_photos.py",
    )
    tampered_message = artifact_approval_payload(
        approval_id=40,
        action="CREATE_SCRIPT",
        artifact=tampered,
        labels_in=[],
    )

    assert SoftwareKeySigner(key=b"x" * 32, key_id="sw:test").verify(message, signature)
    assert not SoftwareKeySigner(key=b"x" * 32, key_id="sw:test").verify(
        tampered_message,
        signature,
    )


def test_script_run_artifact_captures_sandbox_evidence(tmp_path) -> None:
    workspace = ScriptWorkspaceSourcePort(tmp_path, workspace_id="scripts")
    artifact = make_script_run_artifact(
        title="Run rename photos",
        workspace=workspace,
        run_result={
            "spec_id": "run-123",
            "exit_code": 0,
            "timed_out": False,
            "cancelled": False,
            "output_digest": "sha256:abc",
            "outputs": [{"path": "/out/report.txt", "sha256": "abc", "size": 12}],
        },
    )
    body = json.loads(artifact.content)

    assert artifact.artifact_type is ArtifactType.SCRIPT_RUN
    assert artifact.effect is ArtifactEffect.REVIEW_ONLY
    assert artifact.destination_id == "script-workspace:scripts:runs/run-123"
    assert body["exit_code"] == 0
    assert body["outputs"][0]["path"] == "/out/report.txt"


def test_file_export_artifact_binds_exact_output_destination(tmp_path) -> None:
    workspace = ScriptWorkspaceSourcePort(tmp_path, workspace_id="scripts")
    artifact = make_file_export_artifact(
        title="Write report",
        content="renamed 12 files\n",
        workspace=workspace,
        target_path="out/report.txt",
    )

    assert artifact.artifact_type is ArtifactType.FILE_EXPORT
    assert artifact.destination_id == "script-workspace:scripts:out/report.txt"
    assert artifact.sha256 != make_file_export_artifact(
        title="Write report",
        content="renamed 13 files\n",
        workspace=workspace,
        target_path="out/report.txt",
    ).sha256
