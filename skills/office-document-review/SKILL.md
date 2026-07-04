---
name: skill.office.document_review
description: Review and revise Pages or Word documents through bounded read, edit, and export tools.
mode: guidance
capability_kind: PAGES_EDIT
metadata:
  short-description: Office document review workflow
  workflow-family: native-office
---

Use this workflow when the user asks to review, revise, append to, or export a local Pages or Word document.

Start with document metadata and read-only document text. Identify the frontmost document before proposing edits. Use bounded edit tools such as `pages.append_text` or `word.append_text` only for explicit requested changes, and use export tools only for explicit export requests. Do not run arbitrary AppleScript, VBA, macros, shell commands, or UI scripting to modify documents.

For edits or exports, report the document target and the exact text or output path. Prefer draft/review artifacts when the source content is low-integrity or external. Irreversible edits, exports, or cross-app movement should go through the normal approval path.
