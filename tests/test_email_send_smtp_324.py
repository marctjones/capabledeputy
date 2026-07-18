"""#324 (spike #312) — email.send de-stubbed with a real SMTP actuator behind
the UNCHANGED native contract: inert record-only without config, real delivery
when configured, and an honest `delivered` flag either way. No test opens a real
socket — SMTP is monkeypatched."""

from __future__ import annotations

from uuid import uuid4

import pytest

import capabledeputy.tools.native.email_delivery as ed
from capabledeputy.policy.labels import LabelState
from capabledeputy.tools.native.email import DraftBox, EmailOutbox, make_email_tools
from capabledeputy.tools.native.email_delivery import (
    SmtpConfig,
    deliver,
    load_smtp_config,
)
from capabledeputy.tools.registry import ToolContext


def _ctx() -> ToolContext:
    return ToolContext(session_id=uuid4(), label_state=LabelState())


def _send_handler(outbox: EmailOutbox, drafts: DraftBox):
    return next(t for t in make_email_tools(outbox, drafts) if t.name == "email.send").handler


def _handler(outbox: EmailOutbox, drafts: DraftBox, name: str):
    return next(t for t in make_email_tools(outbox, drafts) if t.name == name).handler


_SMTP_ENV = {"CAPDEP_SMTP_HOST": "smtp.example.com", "CAPDEP_SMTP_FROM": "me@example.com"}


# --- config ------------------------------------------------------------------


def test_no_config_is_inert() -> None:
    assert load_smtp_config({}) is None
    # Host without From (and vice-versa) is still not enough.
    assert load_smtp_config({"CAPDEP_SMTP_HOST": "x"}) is None
    assert load_smtp_config({"CAPDEP_SMTP_FROM": "a@b"}) is None


def test_full_config_parses() -> None:
    cfg = load_smtp_config(
        {**_SMTP_ENV, "CAPDEP_SMTP_PORT": "465", "CAPDEP_SMTP_SSL": "on", "CAPDEP_SMTP_USER": "u"}
    )
    assert cfg is not None
    assert cfg.host == "smtp.example.com"
    assert cfg.port == 465
    assert cfg.use_ssl is True
    assert cfg.user == "u"


# --- deliver() ---------------------------------------------------------------


def test_deliver_records_only_without_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CAPDEP_SMTP_HOST", raising=False)
    monkeypatch.delenv("CAPDEP_SMTP_FROM", raising=False)
    result = deliver(to="a@b.com", subject="s", body="b")
    assert result.delivered is False
    assert result.transport == "record-only"


def test_deliver_via_smtp_success(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        ed,
        "send_via_smtp",
        lambda config, *, to, subject, body: calls.append({"to": to, "subject": subject}),
    )
    cfg = SmtpConfig(host="h", port=587, from_addr="me@x")
    result = deliver(to="a@b.com", subject="s", body="b", config=cfg)
    assert result.delivered is True
    assert result.transport == "smtp"
    assert calls == [{"to": "a@b.com", "subject": "s"}]


def test_deliver_reports_smtp_error_without_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(config, *, to, subject, body):
        raise OSError("connection refused")

    monkeypatch.setattr(ed, "send_via_smtp", _boom)
    result = deliver(to="a@b.com", subject="s", body="b", config=SmtpConfig("h", 587, "me@x"))
    assert result.delivered is False
    assert result.transport == "smtp"
    assert "connection refused" in result.detail


# --- handler integration -----------------------------------------------------


async def test_email_send_records_only_without_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CAPDEP_SMTP_HOST", raising=False)
    monkeypatch.delenv("CAPDEP_SMTP_FROM", raising=False)
    outbox = EmailOutbox()
    result = await _send_handler(outbox, DraftBox())(
        {"to": "a@b.com", "subject": "hi", "body": "yo"}, _ctx()
    )
    assert result.output["sent"] is True  # recorded for audit (unchanged)
    assert result.output["delivered"] is False  # but honest: did not leave the machine
    assert result.output["transport"] == "record-only"
    assert len(outbox.all()) == 1  # still audited


async def test_email_send_delivers_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in _SMTP_ENV.items():
        monkeypatch.setenv(k, v)
    sent: list[str] = []
    monkeypatch.setattr(ed, "send_via_smtp", lambda config, *, to, subject, body: sent.append(to))
    outbox = EmailOutbox()
    result = await _send_handler(outbox, DraftBox())(
        {"to": "a@b.com", "subject": "hi", "body": "yo"}, _ctx()
    )
    assert result.output["sent"] is True
    assert result.output["delivered"] is True
    assert result.output["transport"] == "smtp"
    assert sent == ["a@b.com"]
    assert len(outbox.all()) == 1


# --- send_via_smtp real body (smtplib mocked, no socket) ---------------------


class _FakeSMTP:
    """Records the SMTP conversation for assertions."""

    log: list[tuple[str, object]] = []  # noqa: RUF012 (test double, reset per test)

    def __init__(self, host: str, port: int, timeout: float = 0, context: object = None) -> None:
        _FakeSMTP.log.append(("connect", (host, port)))

    def __enter__(self) -> _FakeSMTP:
        return self

    def __exit__(self, *_a: object) -> bool:
        return False

    def starttls(self, context: object = None) -> None:
        _FakeSMTP.log.append(("starttls", None))

    def login(self, user: str, password: str) -> None:
        _FakeSMTP.log.append(("login", user))

    def send_message(self, msg: object) -> None:
        _FakeSMTP.log.append(("send", msg["To"]))  # type: ignore[index]


def test_send_via_smtp_starttls_and_login(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeSMTP.log = []
    monkeypatch.setattr(ed.smtplib, "SMTP", _FakeSMTP)
    cfg = SmtpConfig(host="h", port=587, from_addr="me@x", user="u", password="p", starttls=True)
    ed.send_via_smtp(cfg, to="a@b.com", subject="s", body="b")
    kinds = [k for k, _ in _FakeSMTP.log]
    assert kinds == ["connect", "starttls", "login", "send"]
    assert ("send", "a@b.com") in _FakeSMTP.log


def test_send_via_smtp_ssl_path_with_login(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeSMTP.log = []
    monkeypatch.setattr(ed.smtplib, "SMTP_SSL", _FakeSMTP)
    cfg = SmtpConfig(host="h", port=465, from_addr="me@x", user="u", password="p", use_ssl=True)
    ed.send_via_smtp(cfg, to="a@b.com", subject="s", body="b")
    kinds = [k for k, _ in _FakeSMTP.log]
    assert kinds == ["connect", "login", "send"]  # SSL: no starttls, but login


def test_send_via_smtp_ssl_no_login(monkeypatch: pytest.MonkeyPatch) -> None:
    # SSL path with user=None exercises the skip-login branch.
    _FakeSMTP.log = []
    monkeypatch.setattr(ed.smtplib, "SMTP_SSL", _FakeSMTP)
    cfg = SmtpConfig(host="h", port=465, from_addr="me@x", use_ssl=True)
    ed.send_via_smtp(cfg, to="a@b.com", subject="s", body="b")
    assert [k for k, _ in _FakeSMTP.log] == ["connect", "send"]


def test_send_via_smtp_plain_no_tls_no_login(monkeypatch: pytest.MonkeyPatch) -> None:
    # starttls=False + user=None exercises both skip branches.
    _FakeSMTP.log = []
    monkeypatch.setattr(ed.smtplib, "SMTP", _FakeSMTP)
    cfg = SmtpConfig(host="h", port=25, from_addr="me@x", starttls=False)
    ed.send_via_smtp(cfg, to="a@b.com", subject="s", body="b")
    assert [k for k, _ in _FakeSMTP.log] == ["connect", "send"]


def test_bad_port_falls_back_to_587() -> None:
    cfg = load_smtp_config({**_SMTP_ENV, "CAPDEP_SMTP_PORT": "not-a-number"})
    assert cfg is not None
    assert cfg.port == 587


# --- draft workflow (save -> list -> send goes through the same delivery) -----


async def test_draft_save_list_and_send(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CAPDEP_SMTP_HOST", raising=False)
    monkeypatch.delenv("CAPDEP_SMTP_FROM", raising=False)
    outbox, drafts = EmailOutbox(), DraftBox()
    ctx = _ctx()

    saved = await _handler(outbox, drafts, "email.draft_save")(
        {"to": "a@b.com", "subject": "hi", "body": "yo"}, ctx
    )
    assert saved.output["saved"] is True
    draft_id = saved.output["id"]

    listed = await _handler(outbox, drafts, "email.draft_list")({}, ctx)
    assert any(d["id"] == draft_id for d in listed.output["drafts"])

    sent = await _handler(outbox, drafts, "email.draft_send")({"id": draft_id}, ctx)
    assert sent.output["sent"] is True
    assert sent.output["delivered"] is False  # record-only (no SMTP), honest
    assert sent.output["promoted_from_draft"] == draft_id
    assert len(outbox.all()) == 1


async def test_draft_send_bad_and_unknown_id() -> None:
    outbox, drafts = EmailOutbox(), DraftBox()
    bad = await _handler(outbox, drafts, "email.draft_send")({"id": "not-a-uuid"}, _ctx())
    assert bad.output["sent"] is False
    unknown = await _handler(outbox, drafts, "email.draft_send")(
        {"id": "00000000-0000-0000-0000-000000000000"}, _ctx()
    )
    assert unknown.output["sent"] is False


def test_draftbox_update() -> None:
    drafts = DraftBox()
    d = drafts.save(session_id=uuid4(), to="a@b", subject="s", body="b")
    updated = drafts.update(d.id, subject="new subject")
    assert updated is not None
    assert updated.subject == "new subject"
    assert drafts.update(uuid4()) is None  # unknown id
