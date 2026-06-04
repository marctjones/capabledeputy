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

    return {
        "relationship_group.list": relationship_group_list,
        "relationship_group.add_member": relationship_group_add_member,
        "relationship_group.remove_member": relationship_group_remove_member,
    }
