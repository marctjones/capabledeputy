"""Email tools — send + draft workflow (DESIGN.md §7.4).

Send records to an EmailOutbox for audit AND (de-stubbed in #324) delivers via
SMTP when the operator configures it — inert record-only otherwise. The
policy-carrying `email.send` ToolDefinition stays NATIVE deliberately: spike #312
found that wiring send through the upstream adapter would drop the send/commit
contract (social_commitment, approval_route body preview, irreversibility), so
only the actuator is de-stubbed (see `email_delivery.py`). Drafts are a
daemon-persistent DraftBox separate from the outbox: they are LOCAL (non-egressing) so
they don't trip the social-commitment gate; the send action crosses the boundary.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from email.utils import getaddresses
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from anyio import to_thread

from capabledeputy.approval.model import ApprovalAction
from capabledeputy.approval.route import ApprovalPayloadKind, ApprovalRoute
from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.effect_class import EffectClass, Operation
from capabledeputy.policy.labels import LabelState, label_join
from capabledeputy.tools.native.email_delivery import DeliveryResult, SmtpConfig, deliver
from capabledeputy.tools.native.email_journal import EmailOperationJournal
from capabledeputy.tools.registry import ToolContext, ToolDefinition, ToolResult


@dataclass(frozen=True)
class SentEmail:
    id: UUID
    session_id: UUID
    to: str
    subject: str
    body: str
    sent_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Draft:
    id: UUID
    session_id: UUID
    to: str
    subject: str
    body: str
    created_at: datetime
    updated_at: datetime
    label_state: LabelState = field(default_factory=LabelState)
    version: int = 1
    source: dict[str, Any] = field(default_factory=dict)


def single_recipient(value: str) -> str:
    if not isinstance(value, str) or any(c in value for c in "\r\n"):
        raise ValueError("Invalid recipient")
    addresses = getaddresses([value])
    if len(addresses) != 1 or not re.fullmatch(r"[^\s@<>;,]+@[^\s@<>;,]+", addresses[0][1]):
        raise ValueError("MVP replies require exactly one valid recipient")
    return addresses[0][1]


def draft_dict(draft: Draft) -> dict[str, Any]:
    return {
        "id": str(draft.id),
        "version": draft.version,
        "to": draft.to,
        "subject": draft.subject,
        "body": draft.body,
        "updated_at": draft.updated_at.isoformat(),
        **draft.source,
    }


class EmailSourceBox:
    """Daemon-owned snapshots from actual labeled mail reads; never model-authored."""

    def __init__(self, db_path: Path | None = None):
        self._db_path = db_path
        self._sources: dict[str, dict[str, Any]] = {}
        self._epochs: dict[tuple[str, str], str] = {}
        self._lock = threading.RLock()

    def _reload(self):
        if self._db_path is not None:
            with sqlite3.connect(self._db_path) as con:
                con.execute(
                    "CREATE TABLE IF NOT EXISTS email_sources "
                    "(id TEXT PRIMARY KEY, data TEXT NOT NULL)"
                )
                con.execute(
                    "CREATE TABLE IF NOT EXISTS email_mailbox_epochs "
                    "(provider TEXT NOT NULL, folder TEXT NOT NULL, uidvalidity TEXT NOT NULL, "
                    "PRIMARY KEY(provider, folder))"
                )
                self._epochs = {
                    (provider, folder): epoch
                    for provider, folder, epoch in con.execute(
                        "SELECT provider,folder,uidvalidity FROM email_mailbox_epochs"
                    )
                }
                self._sources = {
                    key: json.loads(value)
                    for key, value in con.execute("SELECT id,data FROM email_sources")
                }

    def observe_mailbox(self, provider: str, folder: str, uidvalidity: str) -> None:
        if not uidvalidity:
            return
        with self._lock:
            self._reload()
            self._epochs[(provider, folder)] = str(uidvalidity)
            if self._db_path is not None:
                with sqlite3.connect(self._db_path) as con:
                    con.execute(
                        "INSERT OR REPLACE INTO email_mailbox_epochs VALUES (?,?,?)",
                        (provider, folder, str(uidvalidity)),
                    )

    def capture(
        self,
        *,
        session_id: UUID,
        provider_tool: str,
        message: dict[str, Any],
        labels: LabelState,
        account: dict[str, str],
    ) -> dict[str, Any]:
        source = {
            key: message.get(key, "")
            for key in (
                "folder",
                "uidvalidity",
                "uid",
                "from",
                "reply_to",
                "subject",
                "message_id",
                "references",
                "body",
            )
        }
        self.observe_mailbox(provider_tool, str(source["folder"]), str(source["uidvalidity"] or ""))
        source.update(account)
        source.update(
            id=str(uuid4()),
            session_id=str(session_id),
            provider_tool=provider_tool,
            labels=labels.to_dict(),
            body_present=bool(message.get("body_present")),
            body_truncated=bool(message.get("body_truncated")),
            created_at=datetime.now(UTC).isoformat(),
        )
        with self._lock:
            self._reload()
            self._sources[source["id"]] = source
            if self._db_path is not None:
                with sqlite3.connect(self._db_path) as con:
                    con.execute(
                        "INSERT INTO email_sources VALUES (?,?)", (source["id"], json.dumps(source))
                    )
        return {**source, "stale": False}

    def all(self, session_id: UUID) -> list[dict[str, Any]]:
        with self._lock:
            self._reload()
            return [
                {
                    **s,
                    "stale": bool(
                        self._epochs.get((s["provider_tool"], str(s["folder"])))
                        and self._epochs[(s["provider_tool"], str(s["folder"]))]
                        != str(s["uidvalidity"])
                    ),
                }
                for s in self._sources.values()
                if s["session_id"] == str(session_id)
            ]

    def get(self, source_id: str, session_id: UUID) -> dict[str, Any] | None:
        return next(
            (s for s in self.all(session_id) if s["id"] == source_id and not s["stale"]), None
        )


class EmailOutbox:
    def __init__(self) -> None:
        self._sent: list[SentEmail] = []

    def all(self) -> list[SentEmail]:
        return list(self._sent)

    def append(self, email: SentEmail) -> None:
        self._sent.append(email)


class DraftBox:
    """SQLite-backed in the daemon, ephemeral for no-path test callers. Drafts are
    local-only — they NEVER egress on their own. Promoting a draft to
    a real send requires going through email.send (or the dedicated
    email.draft_send tool), which routes through the same policy
    chokepoint as a fresh send."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._drafts: dict[UUID, Draft] = {}
        self._db_path = db_path
        self._lock = threading.RLock()

    def _reload(self) -> None:
        if self._db_path is None:
            return
        with sqlite3.connect(self._db_path) as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS email_drafts (id TEXT PRIMARY KEY, data TEXT NOT NULL)"
            )
            drafts = {}
            for _, data in con.execute("SELECT id, data FROM email_drafts"):
                raw = json.loads(data)
                draft = Draft(
                    id=UUID(raw["id"]),
                    session_id=UUID(raw["session_id"]),
                    to=raw["to"],
                    subject=raw["subject"],
                    body=raw["body"],
                    created_at=datetime.fromisoformat(raw["created_at"]),
                    updated_at=datetime.fromisoformat(raw["updated_at"]),
                    label_state=LabelState.from_dict(raw["labels"]),
                    version=raw["version"],
                    source=raw.get("source", {}),
                )
                drafts[draft.id] = draft
            self._drafts = drafts

    def _persist(self, draft: Draft) -> None:
        if self._db_path is None:
            return
        with sqlite3.connect(self._db_path) as con:
            con.execute(
                "INSERT OR REPLACE INTO email_drafts VALUES (?, ?)",
                (
                    str(draft.id),
                    json.dumps(
                        {
                            "id": str(draft.id),
                            "session_id": str(draft.session_id),
                            "to": draft.to,
                            "subject": draft.subject,
                            "body": draft.body,
                            "created_at": draft.created_at.isoformat(),
                            "updated_at": draft.updated_at.isoformat(),
                            "labels": draft.label_state.to_dict(),
                            "version": draft.version,
                            "source": draft.source,
                        }
                    ),
                ),
            )

    def save(
        self,
        *,
        session_id: UUID,
        to: str,
        subject: str,
        body: str,
        label_state: LabelState | None = None,
        source: dict[str, Any] | None = None,
    ) -> Draft:
        now = datetime.now(UTC)
        draft = Draft(
            id=uuid4(),
            session_id=session_id,
            to=to,
            subject=subject,
            body=body,
            created_at=now,
            updated_at=now,
            label_state=label_state or LabelState(),
            source=dict(source or {}),
        )
        with self._lock:
            self._reload()
            self._drafts[draft.id] = draft
            self._persist(draft)
        return draft

    def update(
        self, draft_id: UUID, *, expected_version: int | None = None, **fields: Any
    ) -> Draft | None:
        if set(fields) - {"to", "subject", "body"}:
            raise ValueError("Only draft content may be edited")
        with self._lock:
            self._reload()
            existing = self._drafts.get(draft_id)
            if existing is None:
                return None
            if expected_version is not None and existing.version != expected_version:
                raise ValueError("Draft changed; reload before editing")
            updated = replace(
                existing, updated_at=datetime.now(UTC), version=existing.version + 1, **fields
            )
            self._drafts[draft_id] = updated
            self._persist(updated)
            return updated

    def get(self, draft_id: UUID) -> Draft | None:
        with self._lock:
            self._reload()
            return self._drafts.get(draft_id)

    def discard(self, draft_id: UUID) -> bool:
        with self._lock:
            self._reload()
            removed = self._drafts.pop(draft_id, None) is not None
            if removed and self._db_path is not None:
                with sqlite3.connect(self._db_path) as con:
                    con.execute("DELETE FROM email_drafts WHERE id = ?", (str(draft_id),))
            return removed

    def all(self) -> list[Draft]:
        with self._lock:
            self._reload()
            return sorted(self._drafts.values(), key=lambda d: d.updated_at, reverse=True)


def make_email_tools(
    outbox: EmailOutbox,
    drafts: DraftBox,
    smtp_config: SmtpConfig | None = None,
    sources: EmailSourceBox | None = None,
    operations: EmailOperationJournal | Callable[[], EmailOperationJournal | None] | None = None,
    origin_state: Any = None,
) -> list[ToolDefinition]:
    async def _record_and_deliver(sent: SentEmail) -> DeliveryResult:
        """#324 — record for audit, then attempt real SMTP delivery (or
        record-only when SMTP is unconfigured). Delivery runs in a worker thread
        (smtplib blocks). Never raises: the result carries the honest status."""
        outbox.append(sent)
        return await to_thread.run_sync(
            lambda: deliver(to=sent.to, subject=sent.subject, body=sent.body, config=smtp_config)
        )

    async def email_send(args: dict[str, Any], context: ToolContext) -> ToolResult:
        if "_draft_id" in args:
            journal = operations() if callable(operations) else operations
            if journal is None:
                raise ValueError("Durable send journal is unavailable")
            envelope = {
                key: args[key]
                for key in (
                    "account_id",
                    "from_addr",
                    "to",
                    "subject",
                    "body",
                    "in_reply_to",
                    "references",
                    "message_id",
                )
            }
            current_draft = drafts.get(UUID(str(args["_draft_id"])))
            if (
                current_draft is None
                or current_draft.version != int(args["_draft_version"])
                or str(current_draft.session_id) != args["_origin_session"]
                or any(
                    getattr(current_draft, key) != args[key] for key in ("to", "subject", "body")
                )
            ):
                raise ValueError("Reviewed draft changed before submission; request a fresh review")
            if (
                current_draft.source
                and sources is not None
                and sources.get(current_draft.source["source_id"], current_draft.session_id) is None
            ):
                raise ValueError("Source mailbox changed before submission; reread the message")
            if origin_state is not None:
                source_session = origin_state(current_draft.session_id)
                if source_session.status.value not in {"active", "waiting_approval"}:
                    raise ValueError("Source session is no longer active; request a fresh review")
                current_labels = label_join(source_session.label_state, current_draft.label_state)
                if current_labels.to_dict() != args.get("_reviewed_labels"):
                    raise ValueError(
                        "Source labels changed before submission; request a fresh review"
                    )
            operation, created = journal.claim(
                session_id=UUID(str(args["_origin_session"])),
                draft_id=UUID(str(args["_draft_id"])),
                draft_version=int(args["_draft_version"]),
                envelope=envelope,
                account_fingerprint=args["account_fingerprint"],
                message_id=args["message_id"],
            )
            if created:
                try:
                    result = await to_thread.run_sync(
                        lambda: deliver(
                            to=args["to"],
                            subject=args["subject"],
                            body=args["body"],
                            config=smtp_config,
                            from_addr=args["from_addr"],
                            account_fingerprint=args["account_fingerprint"],
                            in_reply_to=args["in_reply_to"],
                            references=args["references"],
                            message_id=operation.message_id,
                        )
                    )
                    operation = journal.finish(operation.id, result.status, result.detail)
                except BaseException:
                    journal.finish(
                        operation.id,
                        "outcome_unknown",
                        "Submission interrupted; inspect the account before any new send",
                    )
                    raise
            return ToolResult(
                output={
                    "sent": operation.status == "smtp_accepted",
                    "delivered": operation.status == "smtp_accepted",
                    "transport": "smtp",
                    "delivery_detail": operation.detail,
                    "status": operation.status,
                    "operation_id": str(operation.id),
                    "id": str(operation.id),
                    "to": args["to"],
                    "subject": args["subject"],
                    "already_submitted": not created,
                }
            )
        sent = SentEmail(
            id=uuid4(),
            session_id=context.session_id,
            to=str(args.get("to", "")),
            subject=str(args.get("subject", "")),
            body=str(args.get("body", "")),
            sent_at=datetime.now(UTC),
        )
        result = await _record_and_deliver(sent)
        return ToolResult(
            output={
                # `sent` = recorded for audit (unchanged); `delivered` = did it
                # actually leave the machine. Honest either way.
                "sent": True,
                "delivered": result.delivered,
                "transport": result.transport,
                "delivery_detail": result.detail,
                "id": str(sent.id),
                "to": sent.to,
                "subject": sent.subject,
            },
        )

    async def email_draft_save(args: dict[str, Any], context: ToolContext) -> ToolResult:
        draft = drafts.save(
            session_id=context.session_id,
            to=str(args.get("to", "")),
            subject=str(args.get("subject", "")),
            body=str(args.get("body", "")),
            label_state=context.label_state,
        )
        return ToolResult(
            output={
                "saved": True,
                "id": str(draft.id),
                "to": draft.to,
                "subject": draft.subject,
                "body": draft.body,
                "version": draft.version,
                "next_step": "Review before sending; saving does not authorize delivery.",
            },
        )

    async def email_draft_list(_args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        visible = [d for d in drafts.all() if d.session_id == _ctx.session_id]
        return ToolResult(
            additional_tags=label_join(*(d.label_state for d in visible)),
            output={
                "drafts": [draft_dict(d) for d in visible],
            },
        )

    async def source_list(_args: dict[str, Any], context: ToolContext) -> ToolResult:
        rows = sources.all(context.session_id) if sources else []
        return ToolResult(
            output={
                "account_ready": smtp_config is not None,
                "readiness_detail": "Sending account configured; exact approval is still required"
                if smtp_config
                else "Configure SMTP before replying",
                "sources": [
                    {k: v for k, v in row.items() if k not in {"labels", "session_id"}}
                    for row in rows
                ],
            },
            additional_tags=label_join(*(LabelState.from_dict(r["labels"]) for r in rows)),
        )

    async def reply_create(args: dict[str, Any], context: ToolContext) -> ToolResult:
        source = sources.get(str(args["source_id"]), context.session_id) if sources else None
        if source is None:
            raise ValueError("Source not found in this session")
        if not source.get("account_id") or not source.get("from_addr"):
            raise ValueError("A configured sending account is required")
        if not source.get("uidvalidity") or not source.get("uid") or not source.get("message_id"):
            raise ValueError("Source has no stable mailbox/thread identity")
        if not source.get("body_present") or source.get("body_truncated"):
            raise ValueError("Read the complete source message before creating a reply")
        recipient = single_recipient(source.get("reply_to") or source["from"])
        thread = str(source["message_id"]).strip()
        if not re.fullmatch(r"<[^<>\s]+>", thread):
            raise ValueError("Source has an invalid Message-ID")
        references = str(source.get("references") or "").strip()
        if references and not all(re.fullmatch(r"<[^<>\s]+>", item) for item in references.split()):
            raise ValueError("Source has invalid References")
        references = " ".join(dict.fromkeys([*references.split(), thread]))
        subject = str(source["subject"])
        if any(c in subject for c in "\r\n"):
            raise ValueError("Source has invalid subject")
        if not subject.lower().startswith("re:"):
            subject = "Re: " + subject
        draft = drafts.save(
            session_id=context.session_id,
            to=recipient,
            subject=subject,
            body=str(args.get("body", "")),
            label_state=label_join(context.label_state, LabelState.from_dict(source["labels"])),
            source={
                "source_id": source["id"],
                "account_id": source["account_id"],
                "from_addr": source["from_addr"],
                "account_fingerprint": source["account_fingerprint"],
                "in_reply_to": thread,
                "references": references,
            },
        )
        return ToolResult(output={"draft": draft_dict(draft)})

    async def draft_update(args: dict[str, Any], context: ToolContext) -> ToolResult:
        draft = drafts.get(UUID(str(args["id"])))
        if draft is None or draft.session_id != context.session_id:
            raise ValueError("Draft not found in this session")
        journal = operations() if callable(operations) else operations
        if journal and journal.blocks_draft(draft.id):
            raise ValueError(
                "This draft has a submitted or uncertain operation; "
                "inspect its status before editing"
            )
        content = {key: args[key] for key in ("to", "subject", "body")}
        if not all(isinstance(value, str) for value in content.values()):
            raise ValueError("Draft content must be text")
        content["to"] = single_recipient(content["to"])
        if any(c in content["subject"] for c in "\r\n"):
            raise ValueError("Invalid subject")
        updated = drafts.update(draft.id, expected_version=int(args["version"]), **content)
        if updated is None:
            raise ValueError("Draft disappeared during edit")
        return ToolResult(output={"draft": draft_dict(updated)})

    async def email_draft_send(args: dict[str, Any], context: ToolContext) -> ToolResult:
        try:
            draft_id = UUID(str(args["id"]))
        except (KeyError, ValueError) as e:
            return ToolResult(output={"sent": False, "error": f"bad id: {e}"})
        draft = drafts.get(draft_id)
        if draft is None:
            return ToolResult(output={"sent": False, "error": "unknown draft id"})
        if draft.session_id != context.session_id:
            return ToolResult(output={"sent": False, "error": "draft belongs to another session"})
        sent = SentEmail(
            id=uuid4(),
            session_id=context.session_id,
            to=draft.to,
            subject=draft.subject,
            body=draft.body,
            sent_at=datetime.now(UTC),
        )
        result = await _record_and_deliver(sent)
        if result.delivered:
            drafts.discard(draft_id)
        return ToolResult(
            output={
                "sent": True,
                "delivered": result.delivered,
                "transport": result.transport,
                "delivery_detail": result.detail,
                "id": str(sent.id),
                "to": sent.to,
                "subject": sent.subject,
                "promoted_from_draft": str(draft_id),
            },
        )

    return [
        ToolDefinition(
            name="email.source_list",
            risk_ids=("RISK-PII-DISCLOSURE",),
            capability_kind=CapabilityKind.IMAP_READ,
            operations=(Operation(EffectClass.FETCH, subtype="email.source_list"),),
            handler=source_list,
            default_reversibility={"degree": "reversible", "agent": "system"},
            effect_class="data.read_local",
            tool_provenance="operator-curated",
            description="List daemon-owned source references in the current mail session.",
            parameters_schema={"type": "object", "properties": {}},
        ),
        ToolDefinition(
            name="email.reply_create",
            risk_ids=("RISK-PII-DISCLOSURE",),
            capability_kind=CapabilityKind.CREATE_FS,
            operations=(Operation(EffectClass.MUTATE_LOCAL, subtype="email.reply_create"),),
            handler=reply_create,
            default_reversibility={"degree": "reversible", "agent": "system"},
            surfaces_destination_id=True,
            effect_class="data.create_local",
            tool_provenance="operator-curated",
            target_template="local-email-drafts://reply",
            description="Create an editable local reply to an explicitly selected daemon source.",
            parameters_schema={
                "type": "object",
                "properties": {"source_id": {"type": "string"}, "body": {"type": "string"}},
                "required": ["source_id"],
            },
        ),
        ToolDefinition(
            name="email.draft_update",
            risk_ids=("RISK-PII-DISCLOSURE",),
            capability_kind=CapabilityKind.CREATE_FS,
            operations=(Operation(EffectClass.MUTATE_LOCAL, subtype="email.draft_update"),),
            handler=draft_update,
            default_reversibility={"degree": "reversible", "agent": "system"},
            surfaces_destination_id=True,
            effect_class="data.create_local",
            tool_provenance="operator-curated",
            target_template="local-email-drafts://edit",
            description=("Save a version-checked local email edit; prior reviews become stale."),
            parameters_schema={
                "type": "object",
                "properties": {
                    key: {"type": "integer" if key == "version" else "string"}
                    for key in ("id", "version", "to", "subject", "body")
                },
                "required": ["id", "version", "to", "subject", "body"],
            },
        ),
        ToolDefinition(
            name="email.send",
            operations=(Operation(EffectClass.COMMUNICATE, subtype="email.send"),),
            risk_ids=("RISK-DATA-EXFIL-AGENT-TOOLS", "RISK-IRREVERSIBLE-SEND"),
            description=(
                "Send an email. Records the send for audit AND delivers it via "
                "SMTP when the operator has configured CAPDEP_SMTP_HOST/_FROM; "
                "otherwise records-only (the result's `delivered` flag is honest "
                "either way). Required args: to, subject, body."
            ),
            capability_kind=CapabilityKind.SEND_EMAIL,
            handler=email_send,
            target_arg="to",
            effect_class="social.send_email",
            default_reversibility={"degree": "irreversible", "agent": "external"},
            social_commitment=True,
            tool_provenance="operator-curated",
            surfaces_destination_id=True,
            approval_route=ApprovalRoute(
                action=ApprovalAction.SEND_EMAIL,
                target_arg="to",
                payload_kind=ApprovalPayloadKind.EMAIL_ENVELOPE,
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["to", "subject", "body"],
            },
        ),
        ToolDefinition(
            name="email.draft_save",
            operations=(Operation(EffectClass.MUTATE_LOCAL, subtype="email.draft_save"),),
            risk_ids=("RISK-PII-DISCLOSURE",),
            surfaces_destination_id=True,
            description=(
                "Save a local email draft. NON-EGRESSING — the draft lives "
                "in the local DraftBox; the social-commitment gate does NOT "
                "fire. Required args: to, subject, body."
            ),
            capability_kind=CapabilityKind.CREATE_FS,
            handler=email_draft_save,
            target_arg="to",
            target_template="local-email-drafts://{to}",
            effect_class="data.create_local",
            default_reversibility={"degree": "reversible", "agent": "system"},
            tool_provenance="operator-curated",
            parameters_schema={
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["to", "subject", "body"],
            },
        ),
        ToolDefinition(
            name="email.draft_list",
            operations=(Operation(EffectClass.FETCH, subtype="email.draft_list"),),
            risk_ids=("RISK-PII-DISCLOSURE",),
            description="List saved drafts. Read-only.",
            capability_kind=CapabilityKind.IMAP_READ,
            handler=email_draft_list,
            effect_class="data.read_local",
            default_reversibility={"degree": "reversible", "agent": "system"},
            tool_provenance="operator-curated",
            parameters_schema={"type": "object", "properties": {}},
        ),
        ToolDefinition(
            name="email.draft_send",
            operations=(Operation(EffectClass.COMMUNICATE, subtype="email.draft_send"),),
            risk_ids=("RISK-DATA-EXFIL-AGENT-TOOLS", "RISK-IRREVERSIBLE-SEND"),
            description=(
                "Promote a saved draft to a real send. Same policy gates "
                "as email.send (irreversible/external social commitment). "
                "Removes the draft on success. Required args: id (string). "
                "Caps for this tool are coarse (target is the wildcard) "
                "because the draft's recipient is not knowable until "
                "lookup time; for fine-grained recipient gating use "
                "email.send directly."
            ),
            capability_kind=CapabilityKind.SEND_EMAIL,
            handler=email_draft_send,
            # No target_arg: the action target is "", caps must be
            # wildcard-pattern'd, overrides target "".
            effect_class="social.send_email",
            default_reversibility={"degree": "irreversible", "agent": "external"},
            social_commitment=True,
            tool_provenance="operator-curated",
            surfaces_destination_id=True,
            parameters_schema={
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
        ),
    ]
