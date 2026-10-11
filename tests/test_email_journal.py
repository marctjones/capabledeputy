"""#477: no SMTP replay across independent processes or uncertain outcomes."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest

from capabledeputy.tools.native.email_journal import EmailOperationJournal


def _claim(journal: EmailOperationJournal, **overrides):
    args = {
        "session_id": uuid4(),
        "draft_id": uuid4(),
        "draft_version": 1,
        "envelope": {"to": "recipient@example.test", "body": "reviewed"},
        "account_fingerprint": "account-a",
        "message_id": "<stable@example.test>",
    }
    args.update(overrides)
    return journal.claim(**args)


def test_cross_instance_concurrent_claim_has_exactly_one_winner(tmp_path: Path):
    path = tmp_path / "state.sqlite"
    first, second = EmailOperationJournal(path), EmailOperationJournal(path)
    session, draft = uuid4(), uuid4()
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(_claim, journal, session_id=session, draft_id=draft)
            for journal in (first, second)
        ]
        results = [future.result() for future in futures]
    assert sorted(created for _, created in results) == [False, True]
    assert results[0][0] == results[1][0]
    operation = results[0][0]
    assert operation.status == "submitting"
    assert operation.message_id == "<stable@example.test>"
    assert len(operation.envelope_hash) == 64
    first.finish(operation.id, "smtp_accepted", "SMTP acknowledged DATA")
    stored = second.for_draft(draft, 1)
    assert stored is not None and stored.status == "smtp_accepted"
    first.close()
    second.close()
    reopened = EmailOperationJournal(path)
    duplicate, created = _claim(reopened, session_id=session, draft_id=draft)
    assert not created and duplicate.status == "smtp_accepted"
    assert duplicate.message_id == operation.message_id
    reopened.close()


def test_restart_recovers_only_inflight_and_never_replays(tmp_path: Path):
    path = tmp_path / "state.sqlite"
    before = EmailOperationJournal(path)
    unknown, _ = _claim(before)
    failed, _ = _claim(before)
    accepted, _ = _claim(before)
    before.finish(failed.id, "failed_before_submission", "login rejected")
    before.finish(accepted.id, "smtp_accepted")
    reader = EmailOperationJournal(path)
    inflight = reader.get(unknown.id)
    assert inflight is not None and inflight.status == "submitting"  # opening readers is harmless
    reader.close()
    before.close()
    after = EmailOperationJournal(path)
    assert after.recover_submitting() == 1
    assert after.recover_submitting() == 0
    recovered = after.get(unknown.id)
    assert recovered is not None and recovered.status == "outcome_unknown"
    assert "Do not replay" in recovered.detail
    failed_recovered = after.get(failed.id)
    assert failed_recovered is not None and failed_recovered.status == "failed_before_submission"
    accepted_recovered = after.get(accepted.id)
    assert accepted_recovered is not None and accepted_recovered.status == "smtp_accepted"
    operation, created = after.claim(
        session_id=unknown.session_id,
        draft_id=unknown.draft_id,
        draft_version=1,
        envelope=unknown.envelope,
        account_fingerprint=unknown.account_fingerprint,
        message_id=unknown.message_id,
    )
    assert not created and operation.status == "outcome_unknown"
    with pytest.raises(ValueError, match="terminal"):
        after.finish(operation.id, "smtp_accepted")
    after.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("session_id", uuid4()),
        ("envelope", {"body": "changed"}),
        ("account_fingerprint", "account-b"),
        ("message_id", "<different@example.test>"),
    ],
)
def test_claim_cannot_replace_frozen_review(field, value):
    journal = EmailOperationJournal()
    original, _ = _claim(journal)
    args = {"session_id": original.session_id, "draft_id": original.draft_id}
    args[field] = value
    with pytest.raises(ValueError, match="frozen"):
        _claim(journal, **args)
    assert journal.get(original.id) == original
    journal.close()


def test_read_filters_ids_and_terminal_transitions():
    journal = EmailOperationJournal()
    session = uuid4()
    operation, created = _claim(journal, session_id=session, operation_id=uuid4(), message_id=None)
    assert created and operation.message_id.startswith("<")
    other, _ = _claim(journal)
    assert journal.get(uuid4()) is None
    assert journal.for_draft(uuid4(), 1) is None
    assert journal.all(session) == [operation]
    assert {item.id for item in journal.all()} == {operation.id, other.id}
    operation.envelope["body"] = "mutated caller copy"
    stored = journal.get(operation.id)
    assert stored is not None and stored.envelope["body"] == "reviewed"
    with pytest.raises(ValueError, match="terminal"):
        journal.finish(operation.id, "submitting")
    with pytest.raises(KeyError):
        journal.finish(uuid4(), "smtp_accepted")
    done = journal.finish(operation.id, "failed_before_submission", "no connection")
    assert journal.finish(operation.id, "failed_before_submission", "changed") == done
    with pytest.raises(ValueError, match="terminal"):
        journal.finish(operation.id, "outcome_unknown")
    journal.close()


@pytest.mark.parametrize("version", [0, -1, True, "1"])
def test_invalid_version(version):
    journal = EmailOperationJournal()
    with pytest.raises(ValueError, match="positive integer"):
        _claim(journal, draft_version=version)
    journal.close()


@pytest.mark.parametrize("status", ["submitting", "smtp_accepted", "outcome_unknown"])
def test_inflight_accepted_and_unknown_block_every_version(tmp_path: Path, status):
    path = tmp_path / "operations.sqlite"
    writer, other = EmailOperationJournal(path), EmailOperationJournal(path)
    original, _ = _claim(writer)
    if status != "submitting":
        writer.finish(original.id, status)
    assert other.blocks_draft(original.draft_id)
    with pytest.raises(ValueError, match="submitted or unresolved"):
        _claim(other, session_id=original.session_id, draft_id=original.draft_id, draft_version=2)
    writer.close()
    other.close()


def test_reconcile_not_sent_retains_unknown_and_requires_new_reviewed_version(tmp_path: Path):
    path = tmp_path / "operations.sqlite"
    journal = EmailOperationJournal(path)
    original, _ = _claim(journal)
    unknown = journal.finish(original.id, "outcome_unknown", "Lost DATA acknowledgement")
    evidence = {
        "reason": "Provider trace confirmed no DATA accepted",
        "decided_by": "test-operator",
    }
    reconciled = journal.reconcile(original.id, "confirmed_not_sent", **evidence)
    assert reconciled.status == "outcome_unknown"
    assert reconciled.updated_at == unknown.updated_at
    assert reconciled.envelope == unknown.envelope and reconciled.message_id == unknown.message_id
    assert reconciled.reconciliation_outcome == "confirmed_not_sent"
    assert reconciled.reconciliation_reason == evidence["reason"]
    assert reconciled.reconciled_by == evidence["decided_by"]
    assert reconciled.reconciled_at is not None
    assert not journal.blocks_draft(original.draft_id)
    assert journal.reconcile(original.id, "confirmed_not_sent", **evidence) == reconciled
    same, created = _claim(journal, session_id=original.session_id, draft_id=original.draft_id)
    assert not created and same.status == "outcome_unknown"
    journal.close()
    reopened = EmailOperationJournal(path)
    assert reopened.get(original.id) == reconciled
    newer, created = _claim(
        reopened,
        session_id=original.session_id,
        draft_id=original.draft_id,
        draft_version=2,
        message_id="<new-version@example.test>",
        envelope={"body": "newly reviewed content"},
    )
    assert created and newer.message_id != original.message_id and newer.status == "submitting"
    assert reopened.blocks_draft(original.draft_id)
    reopened.close()


def test_confirmed_acceptance_never_reenables_draft():
    journal = EmailOperationJournal()
    operation, _ = _claim(journal)
    journal.finish(operation.id, "outcome_unknown")
    accepted = journal.reconcile(
        operation.id,
        "smtp_accepted",
        reason="Provider message trace shows accepted",
        decided_by="human",
    )
    assert (
        accepted.status == "outcome_unknown" and accepted.reconciliation_outcome == "smtp_accepted"
    )
    assert journal.blocks_draft(operation.draft_id)
    with pytest.raises(ValueError, match="immutable"):
        journal.reconcile(operation.id, "confirmed_not_sent", reason="changed", decided_by="human")
    with pytest.raises(ValueError, match="submitted or unresolved"):
        _claim(
            journal, session_id=operation.session_id, draft_id=operation.draft_id, draft_version=2
        )
    journal.close()


@pytest.mark.parametrize(
    "field,value", [("reason", ""), ("reason", None), ("decided_by", " "), ("decided_by", None)]
)
def test_reconciliation_requires_operator_evidence(field, value):
    journal = EmailOperationJournal()
    kwargs = {"reason": "Provider checked", "decided_by": "human", field: value}
    with pytest.raises(ValueError, match="reason and an identified"):
        journal.reconcile(uuid4(), "confirmed_not_sent", **kwargs)
    journal.close()


def test_reconciliation_does_not_change_known_outcome_or_missing_operation():
    journal = EmailOperationJournal()
    operation, _ = _claim(journal)
    with pytest.raises(ValueError, match="must confirm"):
        journal.reconcile(operation.id, "retry", reason="checked", decided_by="human")
    with pytest.raises(KeyError):
        journal.reconcile(uuid4(), "confirmed_not_sent", reason="checked", decided_by="human")
    with pytest.raises(ValueError, match="Only an unknown"):
        journal.reconcile(operation.id, "confirmed_not_sent", reason="checked", decided_by="human")
    journal.close()


def test_failed_retry_must_be_newer_and_same_owner():
    journal = EmailOperationJournal()
    original, _ = _claim(journal, draft_version=2)
    journal.finish(original.id, "failed_before_submission")
    assert not journal.blocks_draft(original.draft_id)
    with pytest.raises(ValueError, match="newer draft version"):
        _claim(journal, session_id=original.session_id, draft_id=original.draft_id, draft_version=1)
    with pytest.raises(ValueError, match="same session"):
        _claim(journal, draft_id=original.draft_id, draft_version=3)
    journal.close()
