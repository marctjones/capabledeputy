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

from pydantic import BaseModel, Field


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


_SCHEMA_REGISTRY: dict[str, type[BaseModel]] = {
    "DoseSummary": DoseSummary,
    "FinancialSummary": FinancialSummary,
    "ContactInfo": ContactInfo,
}


def get_schema(name: str) -> type[BaseModel]:
    if name not in _SCHEMA_REGISTRY:
        raise KeyError(f"unknown declassification schema: {name}")
    return _SCHEMA_REGISTRY[name]


def list_schemas() -> list[str]:
    return sorted(_SCHEMA_REGISTRY.keys())


def schema_to_jsonschema(name: str) -> dict[str, Any]:
    return get_schema(name).model_json_schema()
