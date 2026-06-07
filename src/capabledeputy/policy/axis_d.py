"""Axis D — Decision Context (FR-006, FR-029, FR-033, FR-037, FR-045, T136).

First-class type for the per-decision context axis. Composed at
runtime from deterministic adapter chains (authentication, expectation
matching, reversibility resolution, relationship bindings). MUST NOT
be constructed from AI input (Principle I + FR-012).

Spec lineage:
- FR-006 / FR-045: Axis D is one of the four orthogonal axes a session carries.
- FR-029: Expectation bindings drive `expectedness`.
- FR-033: Relationship groups drive `relationship_group_ids`.
- FR-037: Reversibility is a sub-facet (degree + agent).
- FR-012: All fields are deterministic/human-declared, AI-read-only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, Self


class AuthLevel(StrEnum):
    """Authentication strength of the initiator.

    Monotone order: OPERATOR_CONFIRMED > PRINCIPAL_DIRECT > SYSTEM_INTERNAL > UNAUTHENTICATED.
    Higher authentication = higher trust in the claimed identity.
    """

    UNAUTHENTICATED = "unauthenticated"
    SYSTEM_INTERNAL = "system-internal"  # daemon-internal, no human input
    PRINCIPAL_DIRECT = "principal-direct"  # operator typed it themselves
    OPERATOR_CONFIRMED = "operator-confirmed"  # human pressed Y at a prompt


@dataclass(frozen=True)
class DecisionContext:
    """Per-session decision context.

    All fields are deterministically composed from:
    - Initiator identity and auth level (operator input or runtime fact)
    - Counterparty identity (derived from egress target)
    - Relationship group membership (operator-declared group assignments)
    - Expectedness (from expectation bindings over action+target)
    - Reversibility (from reversibility labels on effect/tool/target/channel)

    This dataclass is frozen: no field may be mutated post-construction.
    Construction happens only in deterministic adapters (T136), never from
    AI-proposed values (Principle I). Missing or indeterminate fields
    default to the most-restrictive position (Principle VI).

    Backward compatibility note: old code may access `reversibility` as a
    dict via the property below. New code should use the split
    `reversibility_degree` and `reversibility_agent` fields directly.

    Constructor accepts both old and new field names:
    - Old: authentication (str), reversibility (dict with degree/agent)
    - New: initiator_authentication (AuthLevel), reversibility_degree, reversibility_agent
    """

    # Who initiated the action. A principal identifier (e.g., user@example.com,
    # system-daemon, org-account-123). Defaults to "unset" (fail-closed).
    initiator: str = "unset"

    # Was the initiator authenticated? Fail-closed default is UNAUTHENTICATED.
    initiator_authentication: AuthLevel = AuthLevel.UNAUTHENTICATED

    # The destination of the action for egress (e.g., slack.com, hr-database).
    # None for local/internal operations. Used in flow rules (FR-033).
    counterparty: str | None = None

    # Operator-declared relationship groups the counterparty belongs to
    # (e.g., {"project-alpha", "vendor-tier-2"}). Empty ⇒ no known group.
    # Used in rule predicates like "share only with project-members".
    relationship_group_ids: frozenset[str] = field(default_factory=frozenset)

    # Was this action expected per the rules? Per FR-029, expectedness is a
    # binary facet that drives anomaly detection and approval routing.
    # Defaults to "anomalous" (fail-closed): assume the worst until proven.
    expectedness: Literal["expected", "anomalous"] = "anomalous"

    # Reversibility degree: how easily can the effect be undone?
    # Monotone order: reversible > costly-reversible > irreversible.
    # Defaults to "irreversible" (fail-closed per FR-037).
    reversibility_degree: Literal["reversible", "costly-reversible", "irreversible"] = (
        "irreversible"
    )

    # Who can reverse the effect? Monotone order: system > human > external.
    # system: the platform can undo it itself.
    # human: the principal or operator must take action.
    # external: a third party must cooperate (may refuse).
    # Defaults to "external" (fail-closed per FR-037).
    reversibility_agent: Literal["system", "human", "external"] = "external"

    def __init__(
        self,
        initiator: str = "unset",
        initiator_authentication: AuthLevel | None = None,
        counterparty: str | None = None,
        relationship_group_ids: frozenset[str] | None = None,
        expectedness: Literal["expected", "anomalous"] = "anomalous",
        reversibility_degree: Literal[
            "reversible", "costly-reversible", "irreversible"
        ] = "irreversible",
        reversibility_agent: Literal["system", "human", "external"] = "external",
        # Backward-compat: old field names
        authentication: str | None = None,
        reversibility: dict[str, str] | None = None,
    ) -> None:
        """Initialize DecisionContext, supporting both old and new field names.

        Backward compatibility allows old code to pass:
        - authentication (str) → initiator_authentication (AuthLevel)
        - reversibility (dict) → reversibility_degree, reversibility_agent

        This is done via __init__ override to accept and normalize kwargs,
        then object.__setattr__ to set frozen dataclass fields.
        """
        # Resolve initiator_authentication from either param
        auth: AuthLevel = AuthLevel.UNAUTHENTICATED
        if initiator_authentication is not None:
            auth = initiator_authentication
        elif authentication is not None:
            try:
                auth = AuthLevel(str(authentication))
            except ValueError:
                auth = AuthLevel.UNAUTHENTICATED

        # Resolve reversibility from either format
        rev_degree: Literal["reversible", "costly-reversible", "irreversible"] = (
            reversibility_degree
        )
        rev_agent: Literal["system", "human", "external"] = reversibility_agent
        if reversibility is not None and isinstance(reversibility, dict):
            rev_degree = str(reversibility.get("degree", reversibility_degree))  # type: ignore
            rev_agent = str(reversibility.get("agent", reversibility_agent))  # type: ignore

        # Resolve relationship_group_ids
        rel_groups = relationship_group_ids if relationship_group_ids is not None else frozenset()

        # Set frozen dataclass fields via object.__setattr__
        object.__setattr__(self, "initiator", initiator)
        object.__setattr__(self, "initiator_authentication", auth)
        object.__setattr__(self, "counterparty", counterparty)
        object.__setattr__(self, "relationship_group_ids", rel_groups)
        object.__setattr__(self, "expectedness", expectedness)
        object.__setattr__(self, "reversibility_degree", rev_degree)
        object.__setattr__(self, "reversibility_agent", rev_agent)

    @property
    def reversibility(self) -> dict[str, str]:
        """Backward-compatibility property: return reversibility as a dict.

        Old code (decision_rules.py) accesses axis_d.reversibility["degree"]
        and .get("degree"). This property provides that interface.
        """
        return {
            "degree": self.reversibility_degree,
            "agent": self.reversibility_agent,
        }

    @property
    def authentication(self) -> str:
        """Backward-compatibility property: return authentication as a string.

        Old code may have accessed axis_d.authentication. Map it to the
        AuthLevel value for compatibility.
        """
        return self.initiator_authentication.value

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict for Session.to_dict() JSON column storage.

        Default-tolerant: missing or null fields on deserialization receive
        safe fail-closed defaults. Round-trip via from_dict() is idempotent.
        """
        return {
            "initiator": self.initiator,
            "initiator_authentication": self.initiator_authentication.value,
            "counterparty": self.counterparty,
            "relationship_group_ids": sorted(self.relationship_group_ids),
            "expectedness": self.expectedness,
            "reversibility_degree": self.reversibility_degree,
            "reversibility_agent": self.reversibility_agent,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> Self:
        """Deserialize from a dict; default-tolerant per Principle VI.

        Missing or unparseable fields receive the fail-closed defaults from
        the dataclass definition. Called by Session.from_dict() when loading
        a session from persistent storage.

        Backward-compat: handles both old (`authentication`, `reversibility` dict)
        and new field names (`initiator_authentication`, `reversibility_degree`/
        `reversibility_agent`).
        """
        if not d:
            return cls()

        # Initiator authentication: support both new and old field names.
        # Try new name first, fall back to old "authentication" field.
        auth_str = str(
            d.get("initiator_authentication") or d.get("authentication", "unauthenticated")
        )
        try:
            auth = AuthLevel(auth_str)
        except (ValueError, KeyError):
            auth = AuthLevel.UNAUTHENTICATED

        try:
            expectedness_str = str(d.get("expectedness", "anomalous"))
            if expectedness_str not in ("expected", "anomalous"):
                expectedness_str = "anomalous"
        except (ValueError, KeyError):
            expectedness_str = "anomalous"

        # Reversibility: support both old dict format and new split fields.
        rev_degree_str = "irreversible"
        rev_agent_str = "external"

        # New format: split fields
        if "reversibility_degree" in d:
            rev_degree_str = str(d.get("reversibility_degree", "irreversible"))
            if rev_degree_str not in ("reversible", "costly-reversible", "irreversible"):
                rev_degree_str = "irreversible"

        if "reversibility_agent" in d:
            rev_agent_str = str(d.get("reversibility_agent", "external"))
            if rev_agent_str not in ("system", "human", "external"):
                rev_agent_str = "external"

        # Old format: dict with degree/agent keys
        if "reversibility" in d and isinstance(d["reversibility"], dict):
            rev_dict = d["reversibility"]
            rev_degree_str = str(rev_dict.get("degree", "irreversible"))
            if rev_degree_str not in ("reversible", "costly-reversible", "irreversible"):
                rev_degree_str = "irreversible"
            rev_agent_str = str(rev_dict.get("agent", "external"))
            if rev_agent_str not in ("system", "human", "external"):
                rev_agent_str = "external"

        return cls(
            initiator=str(d.get("initiator", "unset")),
            initiator_authentication=auth,
            counterparty=d.get("counterparty"),
            relationship_group_ids=frozenset(str(g) for g in d.get("relationship_group_ids", [])),
            expectedness=expectedness_str,  # type: ignore
            reversibility_degree=rev_degree_str,  # type: ignore
            reversibility_agent=rev_agent_str,  # type: ignore
        )

    @classmethod
    def fail_closed(cls) -> Self:
        """Return the fail-closed default context (Principle VI).

        When the runtime cannot deterministically compose a decision context
        (e.g., missing authentication fact, unresolvable counterparty), use
        this most-restrictive position: unauthenticated, no counterparty,
        anomalous expectedness, irreversible effect, external agent.

        This is called implicitly when Session.new() receives no axis_d
        argument, and may be called explicitly by adapters that detect
        indeterminate state during context building.
        """
        return cls()
