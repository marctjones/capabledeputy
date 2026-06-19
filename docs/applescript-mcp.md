# AppleScript MCP Server

`capdep mcp-server-applescript` is a reusable substrate for local macOS app
automation. It is intended for operator-defined catalogs and for building
specialized MCP servers. CapDep also ships first-class bounded servers for
Apple Mail, Keynote, and general macOS automation:

- `capdep mcp-server-apple-mail`
- `capdep mcp-server-keynote`
- `capdep mcp-server-macos`

The security boundary is the catalog:

- The server exposes only tools declared in trusted YAML catalogs.
- It does not provide a generic `run_applescript` tool.
- Tool arguments are passed to `osascript` as argv values, not interpolated
  into script text.
- Each tool declares its input schema, timeout, output format, and MCP safety
  annotations.
- macOS still enforces Automation/TCC prompts for the terminal process that
  launches the server.

The specialized servers share the same bounded-execution model but package
common workflows directly in Python so they do not require a separate catalog
file.

## Run Standalone

```bash
CAPDEP_APPLESCRIPT_CATALOG=configs/applescript/apple-mail-readonly.yaml \
  capdep mcp-server-applescript
```

Use multiple catalogs with `CAPDEP_APPLESCRIPT_CATALOGS`, separated by the
platform path separator (`:` on macOS):

```bash
CAPDEP_APPLESCRIPT_CATALOGS="configs/applescript/apple-mail-readonly.yaml:configs/applescript/keynote-readonly.yaml" \
  capdep mcp-server-applescript
```

## Use Behind CapDep

Copy the example config:

```bash
mkdir -p ~/.config/capabledeputy/servers.d
cp configs/servers.d.example/applescript.yaml \
  ~/.config/capabledeputy/servers.d/applescript.yaml
```

Then point `CAPDEP_APPLESCRIPT_CATALOGS` at catalogs you trust and restart the
daemon. The example maps read-only catalog tools to built-in capability kinds:

- Apple Mail reads -> `IMAP_READ`
- Keynote document reads -> `READ_FS`

Write or active automation should be placed in separate catalogs and mapped to
`MACOS_AUTOMATION` or another appropriate built-in kind so the normal explicit
grant and first-use gates apply.

For the packaged servers, copy the matching per-server config:

```bash
cp configs/servers.d.example/apple-mail.yaml ~/.config/capabledeputy/servers.d/
cp configs/servers.d.example/keynote.yaml ~/.config/capabledeputy/servers.d/
cp configs/servers.d.example/macos.yaml ~/.config/capabledeputy/servers.d/
```

## Catalog Shape

```yaml
schema_version: 1
tools:
  - name: keynote.frontmost_document
    app: Keynote
    bundle_id: com.apple.iWork.Keynote
    description: Return metadata about the frontmost open Keynote document.
    read_only: true
    destructive: false
    output_format: json
    timeout_seconds: 10
    input_schema:
      type: object
      properties: {}
      additionalProperties: false
    script: |
      on run argv
        tell application "Keynote"
          if not (exists front document) then
            return "{\"open\":false}"
          end if
          set docName to name of front document
        end tell
        return "{\"open\":true}"
      end run
```

If a tool needs arguments, declare properties in `input_schema` and list the
argument names in `argv`. AppleScript receives them in the same order through
`on run argv`.
