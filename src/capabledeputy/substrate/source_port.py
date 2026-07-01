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


# Provider registry. New source substrates (gmail, sharepoint, s3, …)
# add a branch here + a module implementing `SourcePort`; callers select
# by `kind` (e.g. from daemon.yaml). The port import stays free of the
# concrete impls (lazy import) so there is no cycle.
def get_source_port(kind: str, **kwargs: object) -> SourcePort:
    """Construct a SourcePort provider. Fail-closed on unknown kind
    (Constitution VI) — never silently no-op the canonical-id guarantee."""
    if kind == "git":
        from capabledeputy.substrate.git_source import GitSourcePort

        return GitSourcePort(**kwargs)  # type: ignore[arg-type]
    if kind in {"script-workspace", "safe-scripting", "code-workspace"}:
        from capabledeputy.substrate.script_workspace import ScriptWorkspaceSourcePort

        return ScriptWorkspaceSourcePort(**kwargs)  # type: ignore[arg-type]
    if kind in {"gmail", "google_gmail", "google-gmail"}:
        from capabledeputy.substrate.google_source import GmailSourcePort

        return GmailSourcePort(**kwargs)  # type: ignore[arg-type]
    if kind in {"drive", "google_drive", "google-drive", "gdrive"}:
        from capabledeputy.substrate.google_source import GoogleDriveSourcePort

        return GoogleDriveSourcePort(**kwargs)  # type: ignore[arg-type]
    if kind in {"calendar", "google_calendar", "google-calendar"}:
        from capabledeputy.substrate.google_source import GoogleCalendarSourcePort

        return GoogleCalendarSourcePort(**kwargs)  # type: ignore[arg-type]
    if kind in {"browser", "browser.current-page", "browser-current-page"}:
        from capabledeputy.substrate.active_context import BrowserCurrentPageSourcePort

        return BrowserCurrentPageSourcePort()
    if kind in {"macos", "macos.frontmost-app", "macos-frontmost-app"}:
        from capabledeputy.substrate.active_context import MacOSAppContextSourcePort

        return MacOSAppContextSourcePort()
    if kind in {
        "apple-mail",
        "mail",
        "finder",
        "pages",
        "numbers",
        "keynote",
        "calendar",
        "apple-calendar",
    }:
        from capabledeputy.substrate.active_context import source_port_for_active_context

        return source_port_for_active_context(kind)
    known = [
        "git",
        "script-workspace",
        "gmail",
        "google-drive",
        "google-calendar",
        "browser.current-page",
        "macos.frontmost-app",
        "apple-mail",
        "finder",
        "pages",
        "numbers",
        "keynote",
        "calendar",
    ]
    raise ValueError(f"unknown source-port provider {kind!r}; known: {known}")
