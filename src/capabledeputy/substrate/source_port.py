"""Source port — interface only (003 US6 T075 / FR-048).

A SourcePort surfaces *canonical resource handles* and *canonical
destination ids* for an underlying storage / messaging / wire-format
substrate. The TCB sees handles, never raw paths the model typed.
Provider implementations live in spec 004.

Contract (FR-048):
  - `canonicalize_resource(uri)` returns a stable, scheme-normalized
    identifier OR raises if the URI cannot be canonicalized
    (fail-closed; never best-effort).
  - `canonical_destination_id(target)` returns the id the auditor
    should log for a write target — distinct from the model's input
    string.
  - `surfaces_destination_id` is a class-level invariant: a
    ToolDefinition whose target_arg is consumed by this port MUST
    set `surfaces_destination_id=True` so the dispatcher knows the
    audit identity will be authoritative.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class SourcePort(ABC):
    """Port interface. Provider impls (Gmail, SharePoint, S3, ...)
    live in spec 004."""

    surfaces_destination_id: bool = True

    @abstractmethod
    def canonicalize_resource(self, uri: str) -> str:
        """Return a stable canonical id for `uri`. Raise on inputs
        that cannot be canonicalized — never return a guess."""

    @abstractmethod
    def canonical_destination_id(self, target: str) -> str:
        """Return the id the auditor should log for `target`. May be
        the same as `canonicalize_resource` or a more specific value
        (e.g., a resource-id resolved from a path)."""
