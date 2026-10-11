"""Fake SMTP: test phase-sensitive uncertainty without network traffic."""

import smtplib
from dataclasses import replace
from email.message import EmailMessage
from typing import ClassVar

import pytest

from capabledeputy.tools.native import email_delivery as ed


class FakeSMTP:
    fail: ClassVar[str] = ""
    messages: ClassVar[list[EmailMessage]] = []

    def __init__(self, *args, **kwargs):
        if self.fail == "connect":
            raise ConnectionRefusedError("connection refused")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if self.fail == "quit":
            raise ConnectionResetError("QUIT disconnected")

    def starttls(self, **kwargs):
        if self.fail == "tls":
            raise OSError("TLS failed")

    def login(self, *args):
        if self.fail == "login":
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    def send_message(self, message):
        self.messages.append(message)
        if self.fail == "data_reply":
            raise TimeoutError("lost final DATA acknowledgement")
        if self.fail == "data_reject":
            raise smtplib.SMTPDataError(554, b"rejected")
        if self.fail == "recipient":
            raise smtplib.SMTPRecipientsRefused({"recipient@example.test": (550, b"no recipient")})


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.fail = ""
    FakeSMTP.messages = []
    monkeypatch.setattr(ed.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(ed.smtplib, "SMTP_SSL", FakeSMTP)
    return ed.SmtpConfig("smtp.example.test", 587, "sender@example.test", user="sender")


def _deliver(config, **kwargs):
    return ed.deliver(
        to="recipient@example.test",
        subject="Reviewed subject",
        body="Body",
        config=config,
        **kwargs,
    )


@pytest.mark.parametrize("phase", ["connect", "tls", "login", "data_reject", "recipient"])
def test_definite_failures_before_acceptance(smtp, phase):
    FakeSMTP.fail = phase
    result = _deliver(smtp)
    assert result.status == "failed_before_submission"
    assert not result.delivered


def test_lost_data_acknowledgement_is_unknown(smtp):
    FakeSMTP.fail = "data_reply"
    result = _deliver(smtp)
    assert result.status == "outcome_unknown"
    assert not result.delivered
    assert len(FakeSMTP.messages) == 1


def test_disconnect_after_accepted_remains_accepted(smtp):
    FakeSMTP.fail = "quit"
    result = _deliver(smtp)
    assert result.status == "smtp_accepted"
    assert result.delivered
    assert "recipient delivery is unconfirmed" in result.detail


@pytest.mark.parametrize("ssl", [False, True])
def test_reviewed_headers_are_preserved(smtp, ssl):
    result = _deliver(
        replace(smtp, use_ssl=ssl),
        from_addr=smtp.from_addr,
        message_id="<stable@example.test>",
        in_reply_to="<parent@example.test>",
        references="<ancestor@example.test> <parent@example.test>",
        account_fingerprint=ed.smtp_config_fingerprint(replace(smtp, use_ssl=ssl)),
    )
    assert result.status == "smtp_accepted"
    message = FakeSMTP.messages[0]
    assert message["From"] == smtp.from_addr
    assert message["Message-ID"] == "<stable@example.test>"
    assert message["In-Reply-To"] == "<parent@example.test>"
    assert message["References"] == "<ancestor@example.test> <parent@example.test>"


def test_changed_account_and_from_fail_before_connection(smtp):
    fingerprint = ed.smtp_config_fingerprint(smtp)
    changed = replace(smtp, user="different")
    assert _deliver(changed, account_fingerprint=fingerprint).status == "failed_before_submission"
    assert _deliver(smtp, from_addr="different@example.test").status == "failed_before_submission"
    assert FakeSMTP.messages == []
    assert ed.smtp_config_fingerprint(replace(smtp, password="rotated")) == fingerprint
    assert ed.smtp_config_fingerprint(None) == "unconfigured"


def test_invalid_header_is_definite_failure(smtp):
    result = _deliver(smtp, message_id="<ok>\r\nBcc: hidden@example.test")
    assert result.status == "failed_before_submission"
    assert FakeSMTP.messages == []


def test_legacy_delivery_result_defaults_and_unclassified_failure(smtp, monkeypatch):
    assert ed.DeliveryResult(True, "fake").status == "smtp_accepted"
    assert ed.DeliveryResult(False, "fake").status == "failed_before_submission"

    def opaque_actuator(*args, **kwargs):
        raise OSError("No phase evidence")

    monkeypatch.setattr(ed, "send_via_smtp", opaque_actuator)
    assert _deliver(smtp).status == "outcome_unknown"


def test_socket_failure_before_smtp_data_is_definite(smtp, monkeypatch):
    class PhasedSMTP(FakeSMTP):
        def data(self, message):
            raise AssertionError("DATA must not be called")

        def send_message(self, message):
            raise ConnectionResetError("connection lost during MAIL FROM")

    monkeypatch.setattr(ed.smtplib, "SMTP", PhasedSMTP)
    assert _deliver(smtp).status == "failed_before_submission"


def test_socket_failure_during_smtp_data_is_unknown(smtp, monkeypatch):
    class PhasedSMTP(FakeSMTP):
        def data(self, message):
            raise ConnectionResetError("connection lost during DATA")

        def send_message(self, message):
            self.data(message.as_bytes())

    monkeypatch.setattr(ed.smtplib, "SMTP", PhasedSMTP)
    assert _deliver(smtp).status == "outcome_unknown"


def test_operator_environment_never_falls_back_to_account_file(monkeypatch):
    monkeypatch.setenv("CAPDEP_SMTP_HOST", "smtp.explicit.test")
    monkeypatch.setenv("CAPDEP_SMTP_FROM", "sender@explicit.test")
    config = ed.load_operator_smtp_config()
    assert config is not None and config.host == "smtp.explicit.test"
    monkeypatch.delenv("CAPDEP_SMTP_FROM")
    assert ed.load_operator_smtp_config() is None


@pytest.mark.parametrize("contents", [None, "[]", "smtp: []", "smtp: {}"])
def test_operator_missing_or_incomplete_file_is_inert(tmp_path, monkeypatch, contents):
    from capabledeputy.mcp_servers import _imap_creds

    for key in list(ed.os.environ):
        if key.startswith("CAPDEP_SMTP_"):
            monkeypatch.delenv(key)
    path = tmp_path / "account.yaml"
    if contents is not None:
        path.write_text(contents)
    monkeypatch.setattr(_imap_creds, "config_path", lambda: path)
    assert ed.load_operator_smtp_config() is None


@pytest.mark.parametrize("port", [465, 587])
def test_operator_explicit_account_smtp_config(tmp_path, monkeypatch, port):
    from types import SimpleNamespace

    from capabledeputy.mcp_servers import _imap_creds

    for key in list(ed.os.environ):
        if key.startswith("CAPDEP_SMTP_"):
            monkeypatch.delenv(key)
    path = tmp_path / "account.yaml"
    path.write_text("smtp:\n  host: smtp.account.test\n")
    monkeypatch.setattr(_imap_creds, "config_path", lambda: path)
    monkeypatch.setattr(
        _imap_creds,
        "load_config",
        lambda: SimpleNamespace(
            smtp=SimpleNamespace(
                host="smtp.account.test",
                port=port,
                username="sender@account.test",
                password="fixture",
            )
        ),
    )
    config = ed.load_operator_smtp_config()
    assert config is not None
    assert config.host == "smtp.account.test"
    assert config.from_addr == "sender@account.test"
    assert config.password == "fixture"
    assert config.use_ssl == (port == 465)
    assert config.starttls == (port != 465)
