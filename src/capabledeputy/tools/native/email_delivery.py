"""#324 (spike #312) — real email delivery behind the native `email.send`
contract.

Spike #312's finding: wiring email SEND through the upstream adapter would drop
the send/commit contract (`social_commitment`, the `approval_route` body
preview, irreversibility) — a safety regression on the single most dangerous
action. So the policy-carrying `email.send` ToolDefinition stays NATIVE and only
its *actuator* is de-stubbed here: real SMTP delivery when the operator has
configured it, and an honest record-only fallback otherwise (never a silent
"sent: true" that didn't leave the machine).

Configuration is operator env (no secrets in code):
  CAPDEP_SMTP_HOST      required to enable real delivery
  CAPDEP_SMTP_PORT      default 587
  CAPDEP_SMTP_USER      optional (login)
  CAPDEP_SMTP_PASSWORD  optional (login)
  CAPDEP_SMTP_FROM      required — the envelope/from address
  CAPDEP_SMTP_STARTTLS  default "on" (STARTTLS); "off" to disable
  CAPDEP_SMTP_SSL       default "off"; "on" for implicit TLS (usually port 465)
"""

from __future__ import annotations

import hashlib
import json
import os
import smtplib
import ssl
from collections.abc import Buffer, Callable
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import make_msgid
from typing import cast


def _truthy(raw: str | None, *, default: bool) -> bool:
    token = (raw or "").strip().lower()
    if not token:
        return default
    return token in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    from_addr: str
    user: str | None = None
    password: str | None = None
    starttls: bool = True
    use_ssl: bool = False


@dataclass(frozen=True)
class DeliveryResult:
    delivered: bool  # did it actually leave the machine?
    transport: str  # "smtp" | "record-only"
    detail: str = ""
    status: str = ""

    def __post_init__(self) -> None:
        # Keep three-argument test/connector callers compatible.
        if not self.status:
            object.__setattr__(
                self,
                "status",
                "smtp_accepted" if self.delivered else "failed_before_submission",
            )


def smtp_config_fingerprint(config: SmtpConfig | None) -> str:
    """Bind review to the configured account and transport, never its password."""
    if config is None:
        return "unconfigured"
    identity = {
        "host": config.host,
        "port": config.port,
        "from": config.from_addr,
        "user": config.user,
        "starttls": config.starttls,
        "ssl": config.use_ssl,
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


class SmtpSubmissionError(Exception):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status


def load_smtp_config(environ: dict[str, str] | None = None) -> SmtpConfig | None:
    """Build an SmtpConfig from the operator env, or None when SMTP is not
    configured (host + from are the minimum). Inert-by-default: no config ⇒
    email.send records-only, exactly as the stub did."""
    src = os.environ if environ is None else environ
    host = (src.get("CAPDEP_SMTP_HOST") or "").strip()
    from_addr = (src.get("CAPDEP_SMTP_FROM") or "").strip()
    if not host or not from_addr:
        return None
    try:
        port = int(src.get("CAPDEP_SMTP_PORT") or "587")
    except ValueError:
        port = 587
    return SmtpConfig(
        host=host,
        port=port,
        from_addr=from_addr,
        user=(src.get("CAPDEP_SMTP_USER") or "").strip() or None,
        password=src.get("CAPDEP_SMTP_PASSWORD") or None,
        starttls=_truthy(src.get("CAPDEP_SMTP_STARTTLS"), default=True),
        use_ssl=_truthy(src.get("CAPDEP_SMTP_SSL"), default=False),
    )


def _build_message(
    config: SmtpConfig,
    *,
    to: str,
    subject: str,
    body: str,
    from_addr: str | None = None,
    message_id: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = from_addr if from_addr is not None else config.from_addr
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = message_id or make_msgid()
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    msg.set_content(body)
    return msg


def load_operator_smtp_config() -> SmtpConfig | None:
    """Daemon-only actuator configuration, not a grant of send authority.

    Explicit environment settings take precedence. Otherwise reuse the operator's
    IMAP setup only when it explicitly declares an SMTP host. No provider defaults.
    Unit-test Apps never call this loader implicitly.
    """
    if any(key.startswith("CAPDEP_SMTP_") for key in os.environ):
        return load_smtp_config()
    import yaml

    from capabledeputy.mcp_servers._imap_creds import config_path, load_config

    path = config_path()
    if not path.is_file():
        return None
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("smtp"), dict):
        return None
    if not raw["smtp"].get("host"):
        return None
    cfg = load_config().smtp
    return SmtpConfig(
        host=cfg.host,
        port=cfg.port,
        from_addr=cfg.username,
        user=cfg.username,
        password=cfg.password,
        use_ssl=cfg.port == 465,
        starttls=cfg.port != 465,
    )


def send_via_smtp(
    config: SmtpConfig,
    *,
    to: str,
    subject: str,
    body: str,
    from_addr: str | None = None,
    message_id: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> None:
    """Submit once; distinguish definite failure from lost SMTP acknowledgement.

    Success means the SMTP server accepted the submission, not that the recipient
    received it. After send_message starts, an unexplained error is uncertain.
    A disconnect during QUIT cannot undo an already observed acceptance.
    """
    accepted = False
    submitting = False
    try:
        msg = _build_message(
            config,
            to=to,
            subject=subject,
            body=body,
            from_addr=from_addr,
            message_id=message_id,
            in_reply_to=in_reply_to,
            references=references,
        )
        connection = (
            smtplib.SMTP_SSL(
                config.host, config.port, timeout=30, context=ssl.create_default_context()
            )
            if config.use_ssl
            else smtplib.SMTP(config.host, config.port, timeout=30)
        )
        with connection as smtp:
            if config.starttls and not config.use_ssl:
                smtp.starttls(context=ssl.create_default_context())
            if config.user:
                smtp.login(config.user, config.password or "")
            # smtplib.send_message also performs EHLO/MAIL/RCPT. Track its
            # DATA boundary so a socket failure before any body submission is
            # a definite pre-submission failure, not an ambiguous delivery.
            original_data = getattr(smtp, "data", None)
            if callable(original_data):

                def tracked_data(msg: Buffer | str) -> tuple[int, bytes]:
                    nonlocal submitting
                    submitting = True
                    return cast(Callable[[Buffer | str], tuple[int, bytes]], original_data)(msg)

                smtp.data = tracked_data
            else:
                # Alternate/test actuators without DATA phase visibility are
                # conservatively uncertain once send_message is entered.
                submitting = True
            smtp.send_message(msg)
            accepted = True
    except Exception as exc:
        if accepted:
            return
        # These explicit negative replies establish that no recipient accepted
        # DATA; a socket loss/timeout while awaiting the DATA reply does not.
        definite_rejection = isinstance(
            exc,
            (
                smtplib.SMTPSenderRefused,
                smtplib.SMTPRecipientsRefused,
                smtplib.SMTPDataError,
                smtplib.SMTPNotSupportedError,
            ),
        )
        status = (
            "outcome_unknown"
            if submitting and not definite_rejection
            else "failed_before_submission"
        )
        raise SmtpSubmissionError(status, str(exc)) from exc


def deliver(
    *,
    to: str,
    subject: str,
    body: str,
    config: SmtpConfig | None = None,
    from_addr: str | None = None,
    message_id: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
    account_fingerprint: str | None = None,
) -> DeliveryResult:
    """Submit the frozen reviewed envelope once, without automatic retries."""
    cfg = config if config is not None else load_smtp_config()
    if account_fingerprint is not None and account_fingerprint != smtp_config_fingerprint(cfg):
        return DeliveryResult(False, "smtp", "SMTP account changed since review")
    if cfg is None:
        return DeliveryResult(
            delivered=False,
            transport="record-only",
            detail="no SMTP configured (set CAPDEP_SMTP_HOST + CAPDEP_SMTP_FROM to deliver)",
        )
    if from_addr is not None and from_addr != cfg.from_addr:
        return DeliveryResult(False, "smtp", "Reviewed From does not match the configured account")
    # Omit unset optional arguments for compatibility with existing actuators.
    options = {
        key: value
        for key, value in {
            "from_addr": from_addr,
            "message_id": message_id,
            "in_reply_to": in_reply_to,
            "references": references,
        }.items()
        if value is not None
    }
    try:
        send_via_smtp(cfg, to=to, subject=subject, body=body, **options)
    except SmtpSubmissionError as exc:
        return DeliveryResult(False, "smtp", f"smtp error: {exc}", exc.status)
    except Exception as exc:
        # An injected actuator without phase evidence must fail conservatively.
        return DeliveryResult(False, "smtp", f"smtp error: {exc}", "outcome_unknown")
    return DeliveryResult(
        True,
        "smtp",
        f"SMTP accepted via {cfg.host}; recipient delivery is unconfirmed",
        "smtp_accepted",
    )
