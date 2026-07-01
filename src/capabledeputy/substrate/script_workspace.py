"""SourcePort and typed artifact helpers for safe practical scripting."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from capabledeputy.artifacts import ArtifactEffect, ArtifactType, TypedArtifact
from capabledeputy.policy.labels import CategoryTag, LabelState, ProvenanceLevel, ProvenanceTag
from capabledeputy.policy.tiers import Tier
from capabledeputy.substrate.source_port import SourcePort


class ScriptWorkspaceError(RuntimeError):
    """Fail-closed script workspace canonicalization failure."""


_SECRET_FILENAMES = {
    ".env",
    ".env.local",
    ".npmrc",
    ".pypirc",
    ".netrc",
    "id_rsa",
    "id_ed25519",
    "credentials",
}
_SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
_LANGUAGE_CONTENT_TYPES = {
    "python": "text/x-python",
    "sh": "text/x-shellscript",
    "shell": "text/x-shellscript",
    "node": "text/javascript",
    "javascript": "text/javascript",
}


@dataclass(frozen=True)
class WorkspaceFileIdentity:
    """Stable workspace file identity with labels for policy/audit surfaces."""

    canonical_id: str
    relpath: str
    sha256: str
    labels: LabelState

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_id": self.canonical_id,
            "relpath": self.relpath,
            "sha256": self.sha256,
            "labels": self.labels.to_dict(),
        }


class ScriptWorkspaceSourcePort(SourcePort):
    """Canonical IDs for a bounded local workspace used by practical scripts."""

    surfaces_destination_id: bool = True

    def __init__(self, root: Path | str, *, workspace_id: str = "local") -> None:
        resolved = Path(root).expanduser().resolve()
        if not resolved.exists() or not resolved.is_dir():
            raise ScriptWorkspaceError(f"script workspace root does not exist: {resolved}")
        if not workspace_id.strip():
            raise ScriptWorkspaceError("script workspace_id is required")
        self.root = resolved
        self.workspace_id = workspace_id.strip()

    def canonicalize_resource(self, uri: str) -> str:
        return f"script-workspace:{self.workspace_id}:{self._relpath(uri)}"

    def canonical_destination_id(self, target: str) -> str:
        return self.canonicalize_resource(target)

    def identity_for_file(self, path: str) -> WorkspaceFileIdentity:
        relpath = self._relpath(path)
        file_path = self.root / relpath
        if not file_path.is_file():
            raise ScriptWorkspaceError(f"workspace resource is not a file: {path!r}")
        digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
        return WorkspaceFileIdentity(
            canonical_id=f"script-workspace:{self.workspace_id}:{relpath}",
            relpath=relpath,
            sha256=digest,
            labels=labels_for_workspace_path(relpath),
        )

    def _relpath(self, uri: str) -> str:
        raw = uri
        for prefix in ("script-workspace:", "file://"):
            if raw.startswith(prefix):
                raw = raw[len(prefix) :]
        if raw.startswith(f"{self.workspace_id}:"):
            raw = raw[len(self.workspace_id) + 1 :]
        if not raw.strip():
            raise ScriptWorkspaceError(f"empty workspace uri {uri!r}")
        candidate = (self.root / raw).resolve()
        try:
            rel = candidate.relative_to(self.root)
        except ValueError as e:
            raise ScriptWorkspaceError(
                f"workspace resource {uri!r} escapes the script workspace root",
            ) from e
        if not rel.parts:
            raise ScriptWorkspaceError(f"workspace resource {uri!r} resolves to the root")
        return rel.as_posix()


def labels_for_workspace_path(relpath: str) -> LabelState:
    """Conservative default labels for local files visible to generated scripts."""

    path = Path(relpath)
    provenance = ProvenanceTag(ProvenanceLevel.PRINCIPAL_DIRECT)
    if _looks_secret(path):
        return LabelState(
            a=frozenset(
                {
                    CategoryTag(
                        "credentials",
                        Tier.RESTRICTED,
                        risk_ids=("RISK-PRIVILEGE-ESCALATION", "RISK-DATA-EXFIL-AGENT-TOOLS"),
                        assignment_provenance="source-declared",
                    )
                }
            ),
            b=frozenset({provenance}),
        )
    return LabelState(
        a=frozenset(
            {
                CategoryTag(
                    "proprietary_work",
                    Tier.REGULATED,
                    risk_ids=("RISK-PROP-LEAK",),
                    assignment_provenance="source-declared",
                )
            }
        ),
        b=frozenset({provenance}),
    )


def generated_script_labels() -> LabelState:
    return LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.SYSTEM_INTERNAL)}))


def make_script_artifact(
    *,
    title: str,
    code: str,
    language: str,
    workspace: ScriptWorkspaceSourcePort,
    target_path: str,
    labels: LabelState | None = None,
) -> TypedArtifact:
    normalized_language = _normalize_language(language)
    destination_id = workspace.canonical_destination_id(target_path)
    return TypedArtifact(
        artifact_type=ArtifactType.SCRIPT,
        title=title,
        content=code,
        target=target_path,
        destination_id=destination_id,
        effect=ArtifactEffect.CREATE,
        content_type=_LANGUAGE_CONTENT_TYPES[normalized_language],
        labels=labels or generated_script_labels(),
        metadata={"language": normalized_language, "workspace_id": workspace.workspace_id},
    )


def make_script_run_artifact(
    *,
    title: str,
    run_result: dict[str, Any],
    workspace: ScriptWorkspaceSourcePort,
    labels: LabelState | None = None,
) -> TypedArtifact:
    spec_id = str(run_result.get("spec_id") or "")
    if not spec_id:
        raise ScriptWorkspaceError("script run result is missing spec_id")
    summary = {
        "spec_id": spec_id,
        "exit_code": run_result.get("exit_code"),
        "timed_out": bool(run_result.get("timed_out", False)),
        "cancelled": bool(run_result.get("cancelled", False)),
        "output_digest": str(run_result.get("output_digest") or ""),
        "outputs": run_result.get("outputs") or [],
    }
    content = json.dumps(summary, indent=2, sort_keys=True)
    return TypedArtifact(
        artifact_type=ArtifactType.SCRIPT_RUN,
        title=title,
        content=content,
        target=workspace.workspace_id,
        destination_id=f"script-workspace:{workspace.workspace_id}:runs/{spec_id}",
        effect=ArtifactEffect.REVIEW_ONLY,
        content_type="application/json",
        labels=labels or generated_script_labels(),
        metadata={"workspace_id": workspace.workspace_id, "spec_id": spec_id},
    )


def make_file_export_artifact(
    *,
    title: str,
    content: str,
    workspace: ScriptWorkspaceSourcePort,
    target_path: str,
    content_type: str = "text/plain",
    labels: LabelState | None = None,
) -> TypedArtifact:
    return TypedArtifact(
        artifact_type=ArtifactType.FILE_EXPORT,
        title=title,
        content=content,
        target=target_path,
        destination_id=workspace.canonical_destination_id(target_path),
        effect=ArtifactEffect.CREATE,
        content_type=content_type,
        labels=labels or generated_script_labels(),
        metadata={"workspace_id": workspace.workspace_id},
    )


def _normalize_language(language: str) -> str:
    normalized = language.strip().lower()
    if normalized == "javascript":
        normalized = "node"
    if normalized == "shell":
        normalized = "sh"
    if normalized not in _LANGUAGE_CONTENT_TYPES:
        raise ScriptWorkspaceError(f"unsupported scripting language: {language!r}")
    return normalized


def _looks_secret(path: Path) -> bool:
    name = path.name.lower()
    return name in _SECRET_FILENAMES or name.endswith(_SECRET_SUFFIXES)
