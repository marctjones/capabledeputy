"""Inter-host federation primitives (v0.4 foundation, not full sync).

Federation is the long-tail design from DESIGN.md §15: a phone and a
laptop running CapableDeputy with shared session state and approvals.
v0.4 ships the *primitives* needed for the most-asked-for slice —
remote approval handoff — without committing to a full sync protocol:

  - **HostId**: stable identifier per CapableDeputy install. Used as
    the audit attribution string for cross-host operations.
  - **Session export**: signed JSON blob containing a session's
    immutable history + label/capability state. Recipients can replay
    it deterministically against their own audit log.
  - **Session import**: validates the signature, replays the events,
    and registers the session under a tracking marker so subsequent
    decisions know it originated remotely.
  - **Remote approval**: a host can submit an approval request on
    behalf of another host's session, signed by the session's owning
    host. The destination host treats the request like a local one.

What's deliberately NOT in this slice: continuous bidirectional sync,
conflict resolution for parallel state changes, encrypted transport
beyond signature integrity. Those are v0.5+ work.
"""

from __future__ import annotations

from capabledeputy.federation.export import (
    SessionExport,
    SessionExportError,
    export_session,
    import_session_export,
)
from capabledeputy.federation.host import HostId, load_or_create_host_id
from capabledeputy.federation.remote_approval import (
    REMOTE_APPROVAL_SCHEMA_VERSION,
    RemoteApprovalEnvelope,
    pack_remote_approval,
    unpack_remote_approval,
)

__all__ = [
    "REMOTE_APPROVAL_SCHEMA_VERSION",
    "HostId",
    "RemoteApprovalEnvelope",
    "SessionExport",
    "SessionExportError",
    "export_session",
    "import_session_export",
    "load_or_create_host_id",
    "pack_remote_approval",
    "unpack_remote_approval",
]
