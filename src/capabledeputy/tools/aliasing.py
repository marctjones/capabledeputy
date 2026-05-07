"""Per-session unforgeable tool tokens (DESIGN.md §15 — strict ocap).

Each session can opt into name-aliasing: the tool registry's canonical
names like `memory.read` are presented to the LLM under random tokens
like `t_8c3f1a2b`. The harness reverse-maps the token back to the real
name at dispatch. Three properties this gives us:

  1. The LLM cannot reference a tool outside its session's compartment
     because it doesn't know the session-specific name. (Phase 7b's
     capability-driven visibility filter prevents the dispatcher from
     answering such a call; aliasing prevents the LLM from even
     formulating it.)
  2. Strictly deterministic: token = `t_` + first 8 hex chars of
     sha256(session_id || tool_name). Same session + tool → same token.
     Audit logs and traces remain replayable.
  3. Unguessable from outside: the LLM cannot synthesize the token for
     a tool it can't see, since the hash includes the session uuid.

The aliasing is OPTIONAL. Sessions are created with
`tool_aliasing=False` by default; the user opts in per session via the
`capdep session new --tool-tokens` flag (which sets the field).
"""

from __future__ import annotations

import hashlib
from uuid import UUID


def alias_for(session_id: UUID, tool_name: str) -> str:
    """Compute the deterministic alias for one (session, tool) pair."""
    digest = hashlib.sha256(f"{session_id}|{tool_name}".encode()).hexdigest()
    return f"t_{digest[:8]}"


def build_alias_map(session_id: UUID, tool_names: list[str]) -> dict[str, str]:
    """Build a canonical-name → token map for a session's visible tools."""
    return {name: alias_for(session_id, name) for name in tool_names}


def build_reverse_map(session_id: UUID, tool_names: list[str]) -> dict[str, str]:
    """Build a token → canonical-name map for reverse-mapping at dispatch."""
    return {alias_for(session_id, name): name for name in tool_names}
