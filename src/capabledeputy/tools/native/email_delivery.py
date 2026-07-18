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

import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage


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


def _build_message(config: SmtpConfig, *, to: str, subject: str, body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = config.from_addr
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    return msg


def send_via_smtp(config: SmtpConfig, *, to: str, subject: str, body: str) -> None:
    """Deliver one message over SMTP. Blocking — call from a worker thread.
    Raises on failure (the caller records the error honestly)."""
    msg = _build_message(config, to=to, subject=subject, body=body)
    if config.use_ssl:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(config.host, config.port, timeout=30, context=context) as smtp:
            if config.user:
                smtp.login(config.user, config.password or "")
            smtp.send_message(msg)
        return
    with smtplib.SMTP(config.host, config.port, timeout=30) as smtp:
        if config.starttls:
            smtp.starttls(context=ssl.create_default_context())
        if config.user:
            smtp.login(config.user, config.password or "")
        smtp.send_message(msg)


def deliver(
    *,
    to: str,
    subject: str,
    body: str,
    config: SmtpConfig | None = None,
) -> DeliveryResult:
    """Deliver `to/subject/body` via SMTP when configured; otherwise record-only.
    Never raises — a delivery failure is reported in the result so the actuator
    stays honest ('sent' recorded for audit, 'delivered' reflects reality)."""
    cfg = config if config is not None else load_smtp_config()
    if cfg is None:
        return DeliveryResult(
            delivered=False,
            transport="record-only",
            detail="no SMTP configured (set CAPDEP_SMTP_HOST + CAPDEP_SMTP_FROM to deliver)",
        )
    try:
        send_via_smtp(cfg, to=to, subject=subject, body=body)
    except Exception as e:  # smtplib raises a family of exceptions
        return DeliveryResult(delivered=False, transport="smtp", detail=f"smtp error: {e}")
    return DeliveryResult(delivered=True, transport="smtp", detail=f"delivered via {cfg.host}")
