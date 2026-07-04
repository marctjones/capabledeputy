---
name: skill.office.mail_drafting
description: Use bounded Gmail, Apple Mail, or Outlook tools for email triage and draft preparation.
mode: guidance
capability_kind: APPLE_MAIL_DRAFT
metadata:
  short-description: Office mail triage and draft workflow
  workflow-family: native-office
---

Use this workflow when the user asks to triage mail, summarize a thread, or prepare a reply in Gmail, Apple Mail, or Outlook.

Prefer read-only search/list tools first. Read message bodies only when the task needs message content, and summarize what was used before drafting. Create drafts only with bounded draft tools such as `gmail.create_draft`, `apple_mail.create_draft`, or `outlook.create_draft`; do not send mail directly, do not request generic AppleScript, and do not use Office macros.

When drafting, keep recipient, subject, and body explicit in the proposed action. Treat cross-account or external-recipient drafts as policy-relevant egress. If the session carries sensitive, restricted, regulated, prohibited, or low-integrity source data, expect an approval gate before materializing a draft.
