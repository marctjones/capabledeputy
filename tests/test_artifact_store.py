import sqlite3
from contextlib import closing
from dataclasses import replace

import pytest

from capabledeputy.artifact_store import ArtifactStore
from capabledeputy.artifacts import ArtifactEffect, ArtifactError, ArtifactType, TypedArtifact
from capabledeputy.policy.labels import CategoryTag, LabelState
from capabledeputy.policy.tiers import Tier


def test_restart_labels_versions_and_origin_binding(tmp_path):
    path = tmp_path / "outputs.db"
    store = ArtifactStore(path)
    source = LabelState(
        a=frozenset(
            {CategoryTag("health", Tier.RESTRICTED, assignment_provenance="source-declared")}
        )
    )
    artifact = TypedArtifact(
        ArtifactType.RESEARCH, "Result", "first", "review", "local", ArtifactEffect.REVIEW_ONLY
    )
    digest = store.save(artifact, source_session="s1", source_task="t1", source_labels=source)
    reopened = ArtifactStore(path)
    saved = reopened.read_internal(source_session="s1", source_task="t1", digest=digest)
    assert saved.labels == source
    assert saved.content == "first"
    assert (
        reopened.save(artifact, source_session="s1", source_task="t1", source_labels=source)
        == digest
    )
    with pytest.raises(ArtifactError):
        reopened.read_internal(source_session="s2", source_task="t1", digest=digest)
    changed = replace(artifact, content="second")
    next_digest = reopened.save(
        changed, source_session="s1", source_task="t1", source_labels=source
    )
    assert next_digest != digest
    assert (
        reopened.read_internal(source_session="s1", source_task="t1", digest=digest).content
        == "first"
    )
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE results SET payload=replace(payload, 'first', 'other')")
    with pytest.raises(ArtifactError, match="digest mismatch"):
        reopened.read_internal(source_session="s1", source_task="t1", digest=digest)


def test_rejects_public_database(tmp_path):
    path = tmp_path / "outputs.db"
    path.touch(mode=0o644)
    path.chmod(0o644)
    with pytest.raises(ArtifactError, match="owner-only"):
        ArtifactStore(path)
