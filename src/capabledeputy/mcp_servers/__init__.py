"""Standalone Python MCP servers shipped with CapableDeputy.

These servers are the minimal-install story: every one of them is
pure Python, no Node.js / npm / npx dependencies. Each runs as a
stdio MCP server exposable via `capdep mcp-server-<name>`. The same
servers work outside CapableDeputy too (any MCP host that speaks
stdio can launch them).

Available servers:
  - fs       File read/write/list (mode 0o644; absolute paths only)
  - fetch    HTTP fetch via httpx; refuses non-http(s)
  - search   Web search via Brave Search API (DDG fallback option)
  - memory   Persistent key-value store backed by SQLite
  - git      Read-only git operations (status/log/diff/show/branch_list)
  - imap     IMAP mail read/send/organize operations
  - applescript
             Catalog-driven macOS app automation via AppleScript
  - apple_mail
             Bounded Apple Mail automation (read/search/create draft)
  - keynote  Bounded Keynote automation (inspect/present open decks)
  - macos    Bounded macOS desktop primitives (apps/clipboard/notify)

Design notes:
  - Each server is pure-function tool handlers + a small `serve()`
    coroutine wiring up the MCP stdio protocol
  - No CapableDeputy daemon dependency; these are standalone
  - Within CapableDeputy: configure them in upstream_servers/*.yaml
    like any other MCP server. The label/policy framework treats them
    identically to external servers.
"""
