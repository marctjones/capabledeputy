# IMAP + SMTP mail setup (no OAuth)

The bundled `mcp-server-imap` gives you Gmail (or any IMAP server)
without registering an OAuth client in Google Cloud Console. Uses
stdlib `imaplib` + `smtplib` — no extra dependencies.

## When to use this vs. the OAuth Google Workspace path

| | IMAP (this doc) | OAuth (gworkspace) |
|---|---|---|
| **Gmail / email** | yes | yes |
| **Google Docs / Sheets** | no | community mode only |
| **Google Drive** | no | yes |
| **Google Calendar** | no | yes |
| **Setup time** | ~3 min | ~15 min |
| **Cloud Console project required** | no | yes |
| **App Password / 2FA required** | yes (Gmail) | no |
| **Works with non-Google IMAP** | yes | no |

Use IMAP if you only need email and want minimal setup. Use official
Workspace MCP if you need Drive/Calendar/Chat/People. Use
`capdep gworkspace-setup --mode community` only if you need the legacy
Docs/Sheets wrapper.

## Step 1: Enable 2FA on your Gmail account (one-time)

If 2FA isn't enabled, Google won't issue App Passwords.

<https://myaccount.google.com/signinoptions/two-step-verification>

## Step 2: Generate an App Password

1. Visit <https://myaccount.google.com/apppasswords>
2. App: "Mail" (or "Other (Custom name)" → "capdep")
3. Device: "Other"
4. Click "Generate"
5. **Copy the 16-character password** — it's only shown once

The password looks like `abcd efgh ijkl mnop` (spaces are decorative;
they don't need to be in the password file).

## Step 3: Run the setup command

```bash
capdep imap-setup --username you@gmail.com
```

You'll be prompted for the App Password (input hidden). Defaults
assume Gmail (imap.gmail.com:993, smtp.gmail.com:465). For other
providers, pass `--host`, `--port`, `--smtp-host`, `--smtp-port`.
The non-secret daemon block can also be planned or refreshed through the
consolidated setup entry point:

```bash
capdep-setup imap
capdep-setup imap --apply
```

`capdep imap-setup` remains the compatibility path for writing the local IMAP
secret files because it prompts for the App Password.

Examples:

```bash
# Fastmail
capdep imap-setup \
  --host imap.fastmail.com --port 993 \
  --smtp-host smtp.fastmail.com --smtp-port 465 \
  --username you@fastmail.com

# Outlook (where legacy auth is enabled — usually requires admin opt-in)
capdep imap-setup \
  --host outlook.office365.com --port 993 \
  --smtp-host smtp.office365.com --smtp-port 587 \
  --username you@example.com
```

The setup writes two files:
- `~/.config/capabledeputy/secrets/imap-config.yaml` (mode 0o600)
- `~/.config/capabledeputy/secrets/imap-password` (mode 0o600)

## Step 4: Start the daemon

```bash
capdep daemon start --config configs/curated/imap.yaml
capdep tool list | grep "^mail\."
```

You should see 7 tools registered: `mail.imap.list_threads`,
`mail.imap.read_message`, `mail.imap.search`, `mail.imap.send`,
`mail.imap.list_folders`, `mail.imap.mark_read`, `mail.imap.archive`.

## Step 5: Use it

```bash
capdep session new --intent "morning email triage" --purpose inbox
capdep chat
```

Try:
- "Show me my unread email from this morning."
  → Agent calls `mail.imap.list_threads(query="is:unread")`
- "Read the top thread and summarize it."
- "Send a quick reply to the last sender saying I'll respond tomorrow."
  → Chokepoint requires approval (SEND_EMAIL + social-commitment + irreversible)

Each call goes through the chokepoint. Email content carries
`confidential.personal` + `untrusted.user_input` labels — content
authored by external senders that might contain prompt injection
can't propagate to egress without operator override.

## Search syntax

For Gmail, `query` accepts the full Gmail search syntax via the
X-GM-RAW extension:

| Query | Meaning |
|---|---|
| `is:unread` | Unread messages |
| `from:alice@example.com` | From a specific sender |
| `after:2026/05/01` | Messages after a date |
| `subject:"urgent"` | Subject contains "urgent" |
| `has:attachment` | Messages with attachments |
| `label:work` | Tagged with a label |

For non-Gmail IMAP servers, `query` uses standard IMAP SEARCH terms:

| Query | Meaning |
|---|---|
| `UNSEEN` | Unread |
| `FROM "alice"` | From a sender |
| `SINCE 01-May-2026` | Since a date |
| `SUBJECT "urgent"` | Subject contains "urgent" |

## Revoking / rotating

To revoke an App Password:
<https://myaccount.google.com/apppasswords> → trash icon next to the entry.

To rotate locally, just re-run `capdep imap-setup`. The old file is
overwritten.

## Troubleshooting

**"IMAP password file not found"**
You haven't run `capdep imap-setup` yet, or you wrote the config
file by hand at a different location.

**"Authentication failed"**
- Wrong App Password (regenerate)
- 2FA not enabled (Google won't issue App Passwords without it)
- Account uses Workspace + admin has disabled App Passwords (use the
  OAuth path instead)

**"Connection timed out"**
- Wrong host/port (Gmail = imap.gmail.com:993 + smtp.gmail.com:465)
- Network blocks outbound 993/465 (try 587 with STARTTLS for SMTP)

**Gmail says "Less secure app access"**
That's outdated. App Passwords are the modern equivalent and they
DON'T require enabling "less secure app access" (which is gone).
