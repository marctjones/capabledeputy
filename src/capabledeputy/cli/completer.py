"""REPL completer + cache for the chat slash commands.

prompt_toolkit calls `get_completions` synchronously when the user
hits TAB. To keep TAB from blocking on daemon RPCs, every dynamic
lookup (session ids, approval ids, schema names) is served from a
thread-safe cache that a background daemon thread refreshes every
~1 second. The completer never talks to the daemon directly; it just
reads what the cache happens to know.

The cache is best-effort: if the daemon is temporarily slow or
unreachable, completions return whatever was last seen.

This module also exposes the static lists (slash command names,
CapabilityKind values, ApprovalAction values) the completer uses.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from typing import Any

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document

from capabledeputy.policy.capabilities import CapabilityKind

DaemonCall = Callable[[str, dict[str, Any] | None], Any]


SLASH_COMMANDS: tuple[str, ...] = (
    # session control
    "/sessions",
    "/session",
    "/switch",
    "/whoami",
    "/spawn",
    "/abort",
    "/grant",
    "/status",
    "/labels",
    "/caps",
    # approval
    "/approvals",
    "/approve",
    "/deny",
    "/submit",
    "/remember",
    "/override",
    # declassification
    "/schemas",
    "/extract",
    # trace
    "/trace",
    "/audit",
    "/tools",
    # clipboard
    "/copy",
    # misc
    "/help",
    "/quit",
    "/exit",
    "/bye",
)


# Per-command argument completion routing. Keys are command names
# (including leading slash); values are the position-indexed argument
# types to complete (0-indexed in argv after the command).
_SESSION_ARG_CMDS: frozenset[str] = frozenset(
    {"/switch", "/session", "/audit", "/abort"},
)
_APPROVAL_ARG_CMDS: frozenset[str] = frozenset({"/approve", "/deny"})
_KIND_ARG_CMDS: frozenset[str] = frozenset({"/grant"})
_OVERRIDE_SUBCOMMANDS: tuple[str, ...] = ("request", "list", "show", "approve", "deny")
_COPY_SUBCOMMANDS: tuple[str, ...] = ("recovery", "approval", "last", "trace")
_REMEMBER_FLAGS: tuple[str, ...] = (
    "--label-includes",
    "--tag",
    "--ttl-hours",
)
_LABELS: tuple[str, ...] = (
    "confidential.health",
    "confidential.financial",
    "confidential.personal",
    "untrusted.external",
    "untrusted.user_input",
    "trusted.user_direct",
    "egress.email",
    "egress.purchase",
)
_APPROVAL_ACTIONS: tuple[str, ...] = (
    "SEND_EMAIL",
    "QUEUE_PURCHASE",
    "DECLASSIFY",
    "GRANT",
    "MERGE",
)
_GRANT_FLAGS: tuple[str, ...] = (
    "--one-shot",
    "--destructive",
    "--max-amount",
    "--ttl",
    "--rate",
)


class CompletionCache:
    """Background-refreshed view of the daemon state used by the
    completer. Lookups are sub-millisecond (just a dict read under a
    lock); the actual RPC happens off the foreground thread.
    """

    REFRESH_INTERVAL_SECONDS = 1.0

    def __init__(self, daemon_call: DaemonCall) -> None:
        self._call = daemon_call
        self._lock = threading.Lock()
        self._sessions: list[dict[str, Any]] = []
        self._approvals: list[dict[str, Any]] = []
        self._schemas: list[str] = []
        self._inbox: list[dict[str, Any]] = []
        self._tools: list[str] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        # Sync prefetch so the very first TAB has data.
        self._refresh_once()
        self._thread = threading.Thread(
            target=self._refresh_loop,
            daemon=True,
            name="capdep-repl-cache",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _refresh_loop(self) -> None:
        while not self._stop.wait(self.REFRESH_INTERVAL_SECONDS):
            self._refresh_once()

    def _refresh_once(self) -> None:
        sessions = self._safe_call("session.list", {}).get("sessions", [])
        approvals = self._safe_call(
            "approval.list",
            {"status": "pending"},
        ).get("approvals", [])
        schemas = self._safe_call("extract.schemas", {}).get("schemas", [])
        inbox = self._safe_call("extract.inbox_ids", {}).get("messages", [])
        tools_raw = self._safe_call("tool.list", {}).get("tools", [])
        tool_names = sorted(t.get("name", "") for t in tools_raw if t.get("name"))
        with self._lock:
            self._sessions = sessions
            self._approvals = approvals
            self._schemas = schemas
            self._inbox = inbox
            self._tools = tool_names

    def _safe_call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._call(method, params) or {}
        except Exception:
            return {}

    @property
    def sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._sessions)

    @property
    def approval_ids(self) -> list[int]:
        with self._lock:
            return [int(a["id"]) for a in self._approvals]

    @property
    def schemas(self) -> list[str]:
        with self._lock:
            return list(self._schemas)

    @property
    def tool_names(self) -> list[str]:
        with self._lock:
            return list(self._tools)

    @property
    def inbox_messages(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._inbox)


class CapDepCompleter(Completer):
    """prompt_toolkit Completer for the chat REPL.

    Completes slash commands, session UUIDs (with intent shown as
    metadata), approval ids, CapabilityKind values, and schema names.
    Free text (no leading slash) is left alone — that goes to the LLM.
    """

    def __init__(self, cache: CompletionCache) -> None:
        self._cache = cache

    def get_completions(
        self,
        document: Document,
        complete_event: Any,
    ) -> Iterable[Completion]:
        text = document.text_before_cursor
        if not text.startswith("/"):
            return

        # Tokenise the line. argv[0] is the slash command; the rest
        # are positional args. `starting_new` tracks whether the user
        # just typed a space (so the next token is "empty" and we
        # should list all candidates for that arg slot).
        argv = text.split()
        starting_new = text.endswith(" ")

        if len(argv) == 0 or (len(argv) == 1 and not starting_new):
            prefix = argv[0] if argv else "/"
            for cmd in SLASH_COMMANDS:
                if cmd.startswith(prefix):
                    yield Completion(cmd, start_position=-len(prefix))
            return

        cmd = argv[0]
        arg_index = (len(argv) - 1) if starting_new else (len(argv) - 2)
        current = "" if starting_new else argv[-1]

        yield from self._complete_arg(cmd, arg_index, current)

    def _complete_arg(
        self,
        cmd: str,
        arg_index: int,
        current: str,
    ) -> Iterable[Completion]:
        if cmd in _SESSION_ARG_CMDS and arg_index == 0:
            for s in self._cache.sessions:
                sid = s["id"]
                if sid.startswith(current):
                    intent = (s.get("intent") or "").strip()
                    meta = intent[:40] if intent else s.get("status", "")
                    yield Completion(
                        sid,
                        start_position=-len(current),
                        display=f"{sid[:8]} — {meta}" if meta else sid[:8],
                    )
            return

        if cmd in _APPROVAL_ARG_CMDS and arg_index == 0:
            for aid in self._cache.approval_ids:
                s = str(aid)
                if s.startswith(current):
                    yield Completion(s, start_position=-len(current))
            return

        if cmd in _KIND_ARG_CMDS and arg_index == 0:
            up = current.upper()
            for kind in CapabilityKind:
                if kind.value.startswith(up):
                    yield Completion(kind.value, start_position=-len(current))
            return

        if cmd == "/grant" and arg_index >= 2 and current.startswith("--"):
            for flag in _GRANT_FLAGS:
                if flag.startswith(current):
                    yield Completion(flag, start_position=-len(current))
            return

        if cmd == "/extract" and arg_index == 0:
            for m in self._cache.inbox_messages:
                mid = m["id"]
                if mid.startswith(current):
                    subject = (m.get("subject") or "").strip()
                    sender = (m.get("sender") or "").strip()
                    display = f"{mid} — {subject[:40]}" if subject else mid
                    if sender:
                        display = f"{display} ({sender})"
                    yield Completion(
                        mid,
                        start_position=-len(current),
                        display=display,
                    )
            return

        if cmd == "/extract" and arg_index == 1:
            for sch in self._cache.schemas:
                if sch.lower().startswith(current.lower()):
                    yield Completion(sch, start_position=-len(current))
            return

        if cmd == "/remember" and arg_index == 0:
            up = current.upper()
            for action in _APPROVAL_ACTIONS:
                if action.startswith(up):
                    yield Completion(action, start_position=-len(current))
            return

        if cmd == "/remember" and arg_index >= 2 and current.startswith("--"):
            for flag in _REMEMBER_FLAGS:
                if flag.startswith(current):
                    yield Completion(flag, start_position=-len(current))
            return

        # Issue #16 track 5 — contextual completion for newer commands.
        if cmd == "/tools" and arg_index == 0:
            # Complete substring from existing tool names so /tools <filter>
            # narrows correctly. Cheap pull from the registry.
            for tool_name in self._cache.tool_names:
                if current.lower() in tool_name.lower():
                    yield Completion(tool_name, start_position=-len(current))
            return

        if cmd == "/override" and arg_index == 0:
            for sub in _OVERRIDE_SUBCOMMANDS:
                if sub.startswith(current):
                    yield Completion(sub, start_position=-len(current))
            return

        if cmd == "/override" and arg_index == 1:
            # /override request <KIND> | /override show <id>
            sub = "request"
            # If we can introspect previously-typed args, we'd dispatch
            # by subcommand; the prompt-toolkit Completer API only
            # gives us argv (which we already split). For now complete
            # KIND values for `request`/`approve`/`deny`, approval-ish
            # ids for `show`. argv is in scope earlier — we'd need to
            # extend the signature; keep simple by offering both.
            up = current.upper()
            for kind in CapabilityKind:
                if kind.value.startswith(up):
                    yield Completion(kind.value, start_position=-len(current))
            return

        if cmd == "/copy" and arg_index == 0:
            for sub in _COPY_SUBCOMMANDS:
                if sub.startswith(current):
                    yield Completion(sub, start_position=-len(current))
            return

        if cmd == "/copy" and arg_index == 1:
            # /copy approval <id> — complete approval ids
            for aid in self._cache.approval_ids:
                s = str(aid)
                if s.startswith(current):
                    yield Completion(s, start_position=-len(current))
            return

        # Label completion for `--label-includes` arg (next token after the flag).
        # Approximate: if any previous arg matches the flag name, the next
        # current value should complete labels. We can't easily look back
        # in the current Completer signature, so as a soft-completion
        # fallback, when `current` looks like a label prefix (contains '.'),
        # offer the label list.
        if "." in current or any(current.lower() in lbl for lbl in _LABELS):
            for lbl in _LABELS:
                if lbl.startswith(current.lower()):
                    yield Completion(lbl, start_position=-len(current))
            return
