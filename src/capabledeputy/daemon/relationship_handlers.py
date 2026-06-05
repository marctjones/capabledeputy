"""RPC handlers for relationship-group management.

The cookbook P2.3 auto-narrowing affordance lives here: after the
operator approves a send to a previously-unknown counterparty, the
chat REPL offers "remember this recipient as family / work-team /
new group" and the picked answer fires `relationship_group.add_member`.

The mutation surface is OPERATOR-ONLY by construction — the
handlers receive operator-attributable input via the chat REPL or
direct CLI, never via the agent's tool calls. The AI cannot escalate
its own authority by adding itself to a recognized counterparty
group.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from capabledeputy.daemon.handlers import Handler


def make_relationship_handlers(app: Any) -> dict[str, Handler]:
    """Build the relationship_group.* RPC family. Returns an empty
    dict when no RelationshipGroups is wired on the policy context —
    the chat REPL handles the missing endpoint gracefully (the
    auto-narrowing prompt collapses)."""
    policy_context = getattr(app, "policy_context", None)
    if policy_context is None:
        return {}
    registry = getattr(policy_context, "relationship_groups", None)
    if registry is None:
        return {}
    path = getattr(policy_context, "relationship_groups_path", None)

    async def relationship_group_list(_params: dict[str, Any]) -> dict[str, Any]:
        """Return the full registry. Sorted by group_id, with each
        group's members sorted. Read-only — safe for the agent to
        see when the operator wants to expose it (today the agent
        can read via memory etc., but this endpoint is the
        canonical surface)."""
        return {
            "groups": [
                {
                    "group_id": gid,
                    "member_principal_ids": sorted(
                        registry.groups[gid].member_principal_ids,
                    ),
                    # Roadmap v2 #4 — surface per-member tier inline
                    # so the operator sees promotions at a glance.
                    "member_tiers": {
                        pid: registry.tier_for(gid, pid)
                        for pid in sorted(registry.groups[gid].member_principal_ids)
                    },
                }
                for gid in sorted(registry.groups)
            ],
        }

    async def relationship_group_add_member(
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Add `principal_id` to `group_id`. Creates the group if
        absent. Persists the change to relationship_groups.yaml so
        it survives daemon restart.

        Returns:
          added: bool          — True iff membership changed
          group_id: str
          principal_id: str
          persisted: bool      — True iff the YAML write succeeded
          persist_error: str   — only present when persisted=False
        """
        group_id = str(params["group_id"]).strip()
        principal_id = str(params["principal_id"]).strip()
        if not group_id:
            return {
                "added": False,
                "error": "group_id is required",
            }
        if not principal_id:
            return {
                "added": False,
                "error": "principal_id is required",
            }
        added = registry.add_member(group_id, principal_id)
        result: dict[str, Any] = {
            "added": added,
            "group_id": group_id,
            "principal_id": principal_id,
            "persisted": False,
        }
        if added and path is not None:
            try:
                from capabledeputy.policy.relationships import save

                save(registry, Path(path))
                result["persisted"] = True
            except Exception as e:
                result["persist_error"] = str(e)
        elif not added:
            # No-op: principal was already a member. Treat as
            # persisted (the file already reflects this state).
            result["persisted"] = True
        return result

    async def relationship_group_remove_member(
        params: dict[str, Any],
    ) -> dict[str, Any]:
        group_id = str(params["group_id"]).strip()
        principal_id = str(params["principal_id"]).strip()
        removed = registry.remove_member(group_id, principal_id)
        result: dict[str, Any] = {
            "removed": removed,
            "group_id": group_id,
            "principal_id": principal_id,
            "persisted": False,
        }
        if removed and path is not None:
            try:
                from capabledeputy.policy.relationships import save

                save(registry, Path(path))
                result["persisted"] = True
            except Exception as e:
                result["persist_error"] = str(e)
        elif not removed:
            result["persisted"] = True
        return result

    async def relationship_group_tier(params: dict[str, Any]) -> dict[str, Any]:
        """Roadmap v2 #4 — return the reputation tier for
        (group_id, principal_id). Defaults to "unproven" when no
        explicit promotion has happened. Tier informs approval-
        card UX in the REPL (subject-only vs full-body)."""
        group_id = str(params["group_id"]).strip()
        principal_id = str(params["principal_id"]).strip()
        return {
            "group_id": group_id,
            "principal_id": principal_id,
            "tier": registry.tier_for(group_id, principal_id),
        }

    async def relationship_group_effective_tier(
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Highest tier across every group `principal_id` belongs
        to. The REPL uses this for the approval card when the
        agent's send target is a principal, not a group — one
        tier value applies regardless of which group surfaced it."""
        principal_id = str(params["principal_id"]).strip()
        return {
            "principal_id": principal_id,
            "tier": registry.effective_tier_for(principal_id),
            "groups": sorted(registry.resolve(principal_id)),
        }

    async def relationship_group_promote(
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Operator-only tier promotion / demotion. Requires the
        principal to already be a member of the group (the
        operator can add_member separately). Persists to YAML so
        the change survives restart.

        Per Principle VI, the AI must NEVER reach this RPC. The
        chat REPL's `/promote` command is the canonical surface.

        Returns:
          tier: str            — the new tier
          previous_tier: str   — the tier before this call
          group_id, principal_id, persisted: bool
        """
        group_id = str(params["group_id"]).strip()
        principal_id = str(params["principal_id"]).strip()
        tier = str(params["tier"]).strip()
        previous = registry.tier_for(group_id, principal_id)
        try:
            new_tier = registry.set_tier(group_id, principal_id, tier)
        except Exception as e:
            return {
                "group_id": group_id,
                "principal_id": principal_id,
                "error": str(e),
                "persisted": False,
            }
        result: dict[str, Any] = {
            "group_id": group_id,
            "principal_id": principal_id,
            "previous_tier": previous,
            "tier": new_tier,
            "persisted": False,
        }
        if path is not None:
            try:
                from capabledeputy.policy.relationships import save

                save(registry, Path(path))
                result["persisted"] = True
            except Exception as e:
                result["persist_error"] = str(e)
        return result

    async def relationship_group_aggregate_audit(
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Aggregate per-principal audit counts so the operator
        can decide whether to promote. Counts SEND_EMAIL approval
        outcomes where target == principal_id. Defensive against
        an audit log without query support — returns zero counts
        when the daemon has no audit reader.

        Returns:
          principal_id: str
          approved: int
          denied: int
        """
        principal_id = str(params["principal_id"]).strip()
        approved = 0
        denied = 0
        audit_obj = getattr(app, "audit", None)
        read_all = getattr(audit_obj, "read_all", None)
        if read_all is not None:
            try:
                events = await read_all()
            except Exception:
                events = []
            # APPROVAL_APPROVED / DENIED events only carry the
            # approval_id, so first walk APPROVAL_REQUESTED to
            # build the set of approval ids whose target matches
            # this principal. Then tally the decisions against
            # that set.
            target_ids: set[int] = set()
            for ev in events:
                etype = getattr(ev.event_type, "value", None) if hasattr(ev, "event_type") else None
                payload = getattr(ev, "payload", None) or {}
                if not isinstance(payload, dict):
                    continue
                if etype == "approval.requested" and payload.get("target") == principal_id:
                    aid = payload.get("approval_id")
                    if isinstance(aid, int):
                        target_ids.add(aid)
            if target_ids:
                for ev in events:
                    etype = (
                        getattr(ev.event_type, "value", None) if hasattr(ev, "event_type") else None
                    )
                    payload = getattr(ev, "payload", None) or {}
                    if not isinstance(payload, dict):
                        continue
                    aid = payload.get("approval_id")
                    if aid not in target_ids:
                        continue
                    if etype == "approval.approved":
                        approved += 1
                    elif etype == "approval.denied":
                        denied += 1
        return {
            "principal_id": principal_id,
            "approved": approved,
            "denied": denied,
        }

    return {
        "relationship_group.list": relationship_group_list,
        "relationship_group.add_member": relationship_group_add_member,
        "relationship_group.remove_member": relationship_group_remove_member,
        "relationship_group.tier": relationship_group_tier,
        "relationship_group.effective_tier": relationship_group_effective_tier,
        "relationship_group.promote": relationship_group_promote,
        "relationship_group.aggregate_audit": relationship_group_aggregate_audit,
    }
