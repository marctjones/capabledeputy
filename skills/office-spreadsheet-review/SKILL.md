---
name: skill.office.spreadsheet_review
description: Inspect and update Numbers spreadsheets through bounded table and cell tools.
mode: guidance
capability_kind: NUMBERS_EDIT
metadata:
  short-description: Office spreadsheet review workflow
  workflow-family: native-office
---

Use this workflow when the user asks to inspect a spreadsheet, summarize table contents, update a cell, or export a spreadsheet.

Start by listing sheets and table summaries before reading or changing individual cells. Use bounded Numbers tools for table reads, cell reads, cell updates, and PDF export. Do not use formulas, macros, AppleScript snippets, or generic UI automation supplied by the model or the user as executable code.

For writes, identify the sheet, table, cell, and new value before invoking the tool. Treat exported spreadsheets and cross-app copied values as materialized outputs that may need approval when the session contains sensitive or low-integrity data.
