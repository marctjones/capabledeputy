"""Greenfield inline console (TUI redesign).

An inline, streaming, conversational REPL on the same stack (Textual / Rich /
prompt_toolkit), modeled on the Claude-Code feel rather than the panes
dashboard in `tui/app.py`. Built alongside the old UI until parity; nothing
here is wired into the CLI entrypoint yet.

Security-critical foundations land first (see docs/tui-redesign.md §8.1):
- `render.quarantine` — untrusted content can never impersonate trusted chrome.
- `decision` — decision cards are drawn from a typed PolicyDecision, never a
  model-supplied string.
"""
