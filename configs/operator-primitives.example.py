"""Example: operator-curated primitive registration for the spec 004
P0-P3 ports. Drop this into your operator config loader (or import
from it in `daemon/lifecycle.py`) to register all five primitive
ports + the policy-script + OPA hooks.

The operator chooses which primitives to register; the chokepoint
runs whatever is configured. Comment out any block to disable that
primitive entirely.

Usage:
    from configs.operator_primitives_example import build_policy_context
    policy_context = build_policy_context()
"""

from __future__ import annotations

import os

from capabledeputy.policy.context import PolicyContext
from capabledeputy.substrate.decision_inspectors_builtin import (
    AfterHoursPurchaseTightener,
    SelfEgressRelaxer,
)
from capabledeputy.substrate.declassifiers_builtin import (
    RegexRedactor,
    SchemaProjector,
)
from capabledeputy.substrate.elicitation_mediators_builtin import (
    RefuseAllElicitationMediator,
)
from capabledeputy.substrate.sampling_mediators_builtin import (
    AllowlistSamplingMediator,
    LiteLLMSamplingMediator,
    RefuseAllSamplingMediator,
)


def build_policy_context(
    llm_client=None,
    operator_email: str = "operator@example.com",
    enable_opa: bool = False,
):
    """Build a PolicyContext with the operator's primitive registrations.

    Args:
        llm_client: The daemon's main LLMClient (needed for sampling).
        operator_email: Email address the operator considers "self" for
                        the self-egress relaxer.
        enable_opa: When True, attach an OPA sidecar consultant.
                    Requires OPA running at $OPA_ENDPOINT.
    """
    decision_inspectors = [
        # Auto-allow email to operator's own address — avoids the
        # REQUIRE_APPROVAL prompt for self-correspondence.
        SelfEgressRelaxer(self_addresses=frozenset({operator_email})),
        # Late-night purchases (22:00 - 06:00 UTC) get extra scrutiny.
        AfterHoursPurchaseTightener(start_hour_utc=22, end_hour_utc=6),
    ]

    # Optionally consult OPA for an additional layer of policy.
    if enable_opa:
        from capabledeputy.upstream.opa_adapter import OpaConsultingInspector

        decision_inspectors.append(
            OpaConsultingInspector(
                endpoint=os.environ.get("OPA_ENDPOINT", "http://localhost:8181"),
                package=os.environ.get("OPA_PACKAGE", "capabledeputy.policy"),
                timeout_ms=int(os.environ.get("OPA_TIMEOUT_MS", "100")),
            ),
        )

    declassifiers = [
        # Redact common PII patterns (SSN, credit card, phone, email)
        # before propagation. Lowers pii.tier to 'none' on this result.
        RegexRedactor(),
        # If the operator has a specific dict-shaped tool whose output
        # should be projected to a known schema, register a projector
        # per tool / use-case. Example for an inbox-summary tool:
        SchemaProjector(allowed_keys=("subject", "sender", "snippet")),
    ]

    # Sampling — by default, refuse all (no upstream-initiated inference).
    # Enable only for specific trusted upstream servers via an allowlist.
    if llm_client is not None:
        inner_sampling = LiteLLMSamplingMediator(llm_client=llm_client)
        sampling_mediator = AllowlistSamplingMediator(
            allowed_servers=frozenset(
                set(
                    # Add upstream MCP server names the operator wants to allow
                    # sampling from. Empty set = refuse all.
                )
            ),
            inner=inner_sampling,
        )
    else:
        sampling_mediator = RefuseAllSamplingMediator()  # noqa: F841

    # Elicitation — default-safe; refuse all upstream prompts.
    # Switch to ApprovalQueueElicitationMediator (with the queue) once
    # the queue.submit_elicitation() wire-up lands.
    elicitation_mediator = RefuseAllElicitationMediator()  # noqa: F841

    return PolicyContext(
        decision_inspectors=tuple(decision_inspectors),
        declassifiers=tuple(declassifiers),
        # Sampling / Elicitation mediators aren't yet PolicyContext
        # fields (in scope for the next round); they're wired via
        # upstream/manager.py and the elicitation/queue glue
        # respectively.
    )
