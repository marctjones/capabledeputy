# Google Workspace setup

The bundled `mcp-server-gworkspace` exposes Gmail + Docs + Drive +
Calendar to the agent under the policy chokepoint. Operator does
the OAuth dance once; subsequent daemon spawns reuse the cached
refresh token.

## Prerequisites

1. **Create a Google Cloud project**
   <https://console.cloud.google.com/projectcreate>

2. **Enable the APIs you want** (in the Cloud Console — APIs & Services — Library):
   - Gmail API
   - Google Docs API
   - Google Drive API
   - Google Calendar API

3. **Create an OAuth 2.0 Client ID**
   APIs & Services — Credentials — Create Credentials — OAuth client ID
   - Application type: **Desktop application**
   - Name: anything (e.g., "capdep-local")

4. **Configure the OAuth consent screen** (one-time)
   - User type: External (for personal Gmail) or Internal (for Workspace)
   - Add yourself as a test user if External + unverified
   - Add the scopes the server requests, or accept whatever pops up at runtime

5. **Download the credentials JSON**
   Click the download icon next to your new OAuth client. Save the file as:

   ```
   ~/.config/capabledeputy/secrets/gworkspace-credentials.json
   ```

   Set tight permissions:

   ```bash
   chmod 600 ~/.config/capabledeputy/secrets/gworkspace-credentials.json
   ```

## Run the consent flow

```bash
capdep gworkspace-setup
```

This:
- Opens a browser to Google's consent page
- Captures the redirect on `http://localhost:<ephemeral-port>`
- Exchanges the code for a refresh token
- Writes the cached token to `~/.config/capabledeputy/secrets/gworkspace-token.json` (mode 0o600)

Approve the requested scopes. When the browser shows "The
authentication flow has completed", you're done.

## Verify

```bash
capdep daemon start --config configs/curated/google-workspace.yaml
capdep tool list | grep "^google\."
```

You should see ten tools registered under the `google.*` prefix:
`google.gmail.list_threads`, `google.gmail.send`, `google.docs.read`,
`google.calendar.list_events`, etc.

## Use

```bash
capdep session new --intent "morning briefing"
capdep chat
```

Try:
- "Summarize my unread email from this morning."
- "Read the Google Doc with id <DOC_ID> and pull out the action items."
- "What's on my calendar tomorrow?"
- "Search Drive for files named 'budget'."

Each call goes through the chokepoint. Gmail content carries
`confidential.personal` + `untrusted.user_input` labels —
session-tainted reads can't egress without explicit override.
`gmail.send` is destructive + social-commitment, so it goes through
REQUIRE_APPROVAL.

## Revoke / re-do the OAuth flow

Delete the token file:

```bash
rm ~/.config/capabledeputy/secrets/gworkspace-token.json
```

Re-run `capdep gworkspace-setup`. Or revoke the OAuth client at
<https://myaccount.google.com/permissions>.

## Scope tuning

The default scopes (see `src/capabledeputy/mcp_servers/_gworkspace_auth.py`):

```
gmail.modify       (read + label; required for label_thread + send)
gmail.send         (send messages)
documents          (read + write Docs)
drive.file         (manage files created by capdep)
drive.readonly     (read other Drive content)
calendar           (read + write Calendar events)
```

Narrow for least-privilege deployments by editing `DEFAULT_SCOPES`
before running setup.

## Troubleshooting

**"missing OAuth client credentials at ..."**
You haven't saved `credentials.json` yet. See step 5 above.

**"No cached Google token at ..."**
You haven't run `capdep gworkspace-setup` yet.

**Browser opens but says "this app isn't verified"**
Your OAuth consent screen is in "Testing" mode. Add yourself as a
test user (Cloud Console — OAuth consent screen — Test users).

**`refresh_token` is missing after consent**
Google only issues a `refresh_token` on the first consent. If you
already approved this client before, revoke the grant at
<https://myaccount.google.com/permissions>, then re-run setup.
