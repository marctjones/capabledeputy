# AppleScript MCP Catalogs

These catalogs define allowlisted tools for `capdep mcp-server-applescript`.
The server does not expose arbitrary AppleScript execution. Each tool is
operator-authored code with an explicit JSON input schema, argv mapping,
timeout, output format, and MCP safety annotations.

Run one catalog:

```bash
CAPDEP_APPLESCRIPT_CATALOG=configs/applescript/apple-mail-readonly.yaml \
  capdep mcp-server-applescript
```

Run several catalogs:

```bash
CAPDEP_APPLESCRIPT_CATALOGS="configs/applescript/apple-mail-readonly.yaml:configs/applescript/keynote-readonly.yaml" \
  capdep mcp-server-applescript
```

Use this substrate for app-specific servers by shipping small catalogs for
Mail, Keynote, Pages, Numbers, Microsoft Office, or other scriptable macOS
apps. Keep write or active automation in separate catalogs and map them to
app-specific built-in capability kinds such as `APPLE_MAIL_DRAFT`,
`KEYNOTE_PRESENT`, `PAGES_EDIT`, `NUMBERS_EDIT`, or `MACOS_CLIPBOARD_WRITE`
in `servers.d`. `MACOS_AUTOMATION` remains a compatibility umbrella, not the
preferred mapping for new tools.

For production personal-assistant use, prefer the bundled specialized servers
over a broad generic catalog. Pair every write/active tool with a concrete
`target_arg` or `target_template` in the upstream config so approvals and audit
records name the destination (`to`, `path`, `pages://frontmost`,
`macos://clipboard`, etc.). Keep the Starlark `local_app_confirm.star`
inspector enabled so first use of clipboard access, app control, drafts,
document edits/exports, and presentation control requires human confirmation
before the session proceeds.
