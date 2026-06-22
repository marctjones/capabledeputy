"""Declassification schemas for the dual-LLM mode (DESIGN.md §5.2).

Each schema is a Pydantic model that constrains what the quarantined
LLM is allowed to emit when extracting from labeled data. Returning a
schema-validated value IS the declassification gate: the planner LLM
sees only structured fields, never raw text from the labeled source.

Schemas are deliberately small and field-typed. Free-text fields are
permitted but bounded in length so a misbehaving (or compromised)
quarantined LLM cannot smuggle large amounts of source data through.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class UnsafeDeclassificationSchemaError(ValueError):
    pass


def _refuse_flagged_content(
    *,
    executable_content_detected: bool,
    prompt_injection_detected: bool,
    embedded_credentials_detected: bool,
) -> None:
    if executable_content_detected:
        raise UnsafeDeclassificationSchemaError("executable content detected")
    if prompt_injection_detected:
        raise UnsafeDeclassificationSchemaError("prompt injection detected")
    if embedded_credentials_detected:
        raise UnsafeDeclassificationSchemaError("embedded credentials detected")


class DoseSummary(BaseModel):
    medication_name: str = Field(max_length=80)
    dosage_mg: float = Field(ge=0, le=10_000)
    frequency: str = Field(max_length=40)


class FinancialSummary(BaseModel):
    account_kind: str = Field(max_length=40)
    balance_bucket: str = Field(
        max_length=40,
        description="A coarse bucket like 'under-1k', '1k-10k', etc. Never a precise number.",
    )
    last_activity_days_ago: int = Field(ge=0, le=10_000)


class ContactInfo(BaseModel):
    name: str = Field(max_length=80)
    relationship: str = Field(max_length=40)


class DailyBriefing(BaseModel):
    """Coarse summary of a user's day. Field counts and lengths are
    bounded so a misbehaving quarantined LLM can't smuggle large
    amounts of source data through the schema (DESIGN.md §5.2)."""

    date: str = Field(max_length=10)
    n_calendar_events: int = Field(ge=0, le=50)
    n_unread_emails: int = Field(ge=0, le=200)
    top_priority: str = Field(max_length=120)
    suggested_focus: str = Field(max_length=160)


class EmailTriageItem(BaseModel):
    """One row of an inbox triage view. Sender + subject are length-
    bounded; urgency is enum-like via max_length on a string field."""

    sender: str = Field(max_length=120)
    subject: str = Field(max_length=200)
    urgency: str = Field(max_length=10, description="low | medium | high")
    one_line_summary: str = Field(max_length=160)


class EmailForwardable(BaseModel):
    """Forwardable email fields extracted through Pattern ②.

    The quarantined LLM must explicitly attest that the extracted payload does
    not contain executable content, prompt-injection instructions, or embedded
    credentials. A positive flag refuses validation instead of relying on the
    planner or user to notice unsafe material later.
    """

    sender: str = Field(max_length=120)
    recipients: list[str] = Field(default_factory=list, max_length=25)
    date: str = Field(max_length=80)
    subject: str = Field(max_length=200)
    body: str = Field(max_length=10_000)
    executable_content_detected: bool = False
    prompt_injection_detected: bool = False
    embedded_credentials_detected: bool = False

    @model_validator(mode="after")
    def refuse_unsafe_content(self) -> EmailForwardable:
        _refuse_flagged_content(
            executable_content_detected=self.executable_content_detected,
            prompt_injection_detected=self.prompt_injection_detected,
            embedded_credentials_detected=self.embedded_credentials_detected,
        )
        return self


class WebPageSummary(BaseModel):
    """Bounded summary of a fetched untrusted web page."""

    title: str = Field(max_length=200)
    key_facts: list[str] = Field(max_length=5)
    relevant_to_query: bool


class WebPagePublicFacts(BaseModel):
    """Operator-reviewable public facts from an untrusted web page."""

    title: str = Field(max_length=200)
    byline: str | None = Field(default=None, max_length=120)
    published_date: str | None = Field(default=None, max_length=80)
    article_body: str | None = Field(default=None, max_length=8_000)
    excerpts: list[str] = Field(default_factory=list, max_length=10)
    executable_content_detected: bool = False
    prompt_injection_detected: bool = False
    embedded_credentials_detected: bool = False

    @model_validator(mode="after")
    def refuse_unsafe_content(self) -> WebPagePublicFacts:
        _refuse_flagged_content(
            executable_content_detected=self.executable_content_detected,
            prompt_injection_detected=self.prompt_injection_detected,
            embedded_credentials_detected=self.embedded_credentials_detected,
        )
        return self


class FinancialSummaryForAccountant(BaseModel):
    """Summary suitable for sending to an external accountant. Keeps
    individual numbers in coarse buckets so the schema itself acts as
    a privacy filter, not just a structuring step."""

    period: str = Field(max_length=20, description="e.g. 'Q1 2026'")
    total_income_bucket: str = Field(
        max_length=40,
        description="Bucket like 'under-50k', '50k-100k', etc. Never an exact number.",
    )
    total_expenses_bucket: str = Field(max_length=40)
    n_transactions: int = Field(ge=0, le=100_000)
    notable_categories: list[str] = Field(max_length=5)


_SCHEMA_REGISTRY: dict[str, type[BaseModel]] = {
    "DoseSummary": DoseSummary,
    "FinancialSummary": FinancialSummary,
    "ContactInfo": ContactInfo,
    "DailyBriefing": DailyBriefing,
    "EmailForwardable": EmailForwardable,
    "EmailTriageItem": EmailTriageItem,
    "WebPagePublicFacts": WebPagePublicFacts,
    "WebPageSummary": WebPageSummary,
    "FinancialSummaryForAccountant": FinancialSummaryForAccountant,
}


def get_schema(name: str) -> type[BaseModel]:
    if name not in _SCHEMA_REGISTRY:
        raise KeyError(f"unknown declassification schema: {name}")
    return _SCHEMA_REGISTRY[name]


def list_schemas() -> list[str]:
    return sorted(_SCHEMA_REGISTRY.keys())


def schema_to_jsonschema(name: str) -> dict[str, Any]:
    return get_schema(name).model_json_schema()
