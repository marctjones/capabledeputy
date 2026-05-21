"""OPA sidecar adapter (spec 004 P3 follow-up).

When an operator runs Open Policy Agent as a sidecar (or out-of-process)
and wants to use Rego-authored rules to refine CapableDeputy's
chokepoint decisions, this adapter sits between the chokepoint and
OPA's REST API.

Flow:
  1. Chokepoint produces a proposed PolicyDecision via decide()
  2. OpaConsultingInspector serializes (action, session, proposed) as
     JSON
  3. Posts to OPA's /v1/data/<package> endpoint with timeout
  4. Parses OPA's response into a DecisionRelax / DecisionTighten / None
  5. Composes with the standard chokepoint outcome via the existing
     DecisionInspector pathway (TIGHTEN beats RELAX, most-restrictive
     wins)

Fail-closed: timeouts, malformed responses, and HTTP errors are
treated as abstain (no opinion). The chokepoint's standard decision
stands. Operator can configure timeout per-deployment; default 100ms.

OPA is OPTIONAL. CapableDeputy works without it. Operators who want
to author rules in Rego turn this on; everyone else doesn't.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from capabledeputy.substrate.decision_inspector_port import (
    DecisionRelax,
    DecisionTighten,
)


@dataclass
class OpaConsultingInspector:
    """A DecisionInspector backed by an OPA sidecar.

    Operator configures:
      - endpoint: OPA's HTTP endpoint (e.g., http://localhost:8181)
      - package: Rego package path (e.g., "capabledeputy.policy")
      - timeout_ms: per-call timeout (default 100ms; fail-closed)

    The Rego policy MUST return a JSON document with this shape:
        {
          "decision_inspector": "tighten" | "relax" | null,
          "to": "allow" | "deny" | "require_approval" | "override_required",
          "rule": "<operator-supplied rule id>",
          "rationale": "<operator-supplied rationale>"
        }

    Schema mismatches are treated as abstain.
    """

    name: str = "OpaConsultingInspector"
    endpoint: str = "http://localhost:8181"
    package: str = "capabledeputy.policy"
    timeout_ms: int = 100
    extra_input: dict[str, Any] = field(default_factory=dict)

    async def inspect(
        self,
        *,
        action: Any,
        session: Any,
        proposed_outcome: Any,
    ) -> DecisionRelax | DecisionTighten | None:
        """Consult OPA. Fail-closed on any error (returns None)."""
        input_doc = self._serialize(action, session, proposed_outcome)
        url = f"{self.endpoint.rstrip('/')}/v1/data/{self.package.replace('.', '/')}"
        timeout = self.timeout_ms / 1000.0
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json={"input": input_doc})
                if resp.status_code != 200:
                    return None
                payload = resp.json()
        except (httpx.HTTPError, ValueError):
            return None
        return self._parse_response(payload)

    def _serialize(
        self,
        action: Any,
        session: Any,
        proposed_outcome: Any,
    ) -> dict[str, Any]:
        """Build the OPA input document. Operators write Rego that
        reads `input.action`, `input.session`, `input.proposed_outcome`."""
        action_doc = {
            "kind": getattr(action.kind, "value", str(getattr(action, "kind", ""))),
            "target": str(getattr(action, "target", "")),
            "amount": getattr(action, "amount", None),
        }
        session_doc = {
            "id": str(getattr(session, "id", "")),
            "labels": sorted(
                str(getattr(label, "value", label)) for label in getattr(session, "label_set", [])
            ),
            "purpose_handle": str(getattr(session, "purpose_handle", "")),
            "clearance_profile_id": str(getattr(session, "clearance_profile_id", "") or ""),
        }
        proposed_doc = {
            "decision": getattr(
                proposed_outcome.decision,
                "value",
                str(proposed_outcome.decision),
            ),
            "rule": str(getattr(proposed_outcome, "rule", "") or ""),
            "reason": str(getattr(proposed_outcome, "reason", "") or ""),
        }
        return {
            "action": action_doc,
            "session": session_doc,
            "proposed_outcome": proposed_doc,
            **self.extra_input,
        }

    def _parse_response(
        self,
        payload: dict[str, Any],
    ) -> DecisionRelax | DecisionTighten | None:
        """Parse OPA's `data` response into a DecisionInspector outcome.

        Tolerant of OPA's standard {"result": ...} envelope shape.
        """
        from capabledeputy.policy.rules import Decision

        result = payload.get("result", payload)
        if not isinstance(result, dict):
            return None
        kind = result.get("decision_inspector")
        to_str = result.get("to")
        if kind not in ("relax", "tighten"):
            return None
        if not to_str:
            return None
        try:
            to_dec = Decision(to_str)
        except ValueError:
            return None
        rule = str(result.get("rule", "") or "opa")
        rationale = str(result.get("rationale", "") or "")
        if kind == "relax":
            return DecisionRelax(to=to_dec, rule=rule, rationale=rationale)
        return DecisionTighten(to=to_dec, rule=rule, rationale=rationale)
