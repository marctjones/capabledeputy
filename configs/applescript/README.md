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
Mail, Keynote, Microsoft Office, or other scriptable macOS apps. Keep write or
active automation in separate catalogs and map them to `MACOS_AUTOMATION` or
another appropriate built-in capability kind in `servers.d`.
