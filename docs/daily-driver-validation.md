# Daily-Driver Workflow Validation

The daily-driver preset is validated against concrete user-facing workflows, not
only against abstract policy defaults. The validation report is available in
`capdep-setup daily-driver --json` under `details.workflow_validation`.

The report checks:

- every bundled daily-driver workflow has a known purpose;
- the purpose grants the workflow's required capabilities;
- source ports resolve through `configs/personal-assistant/source_bindings.yaml`;
- default purposes do not include direct-send, generic browser automation,
  generic macOS automation, or browser scripting capabilities;
- workflows that mutate state or edit documents surface foreground review;
- egress remains approval-gated; and
- workflow retention keeps source context and artifacts session-scoped while
  audit evidence remains durable.

Current covered workflows:

| Workflow | Expected user posture |
|---|---|
| Morning Briefing | Launches without approval; reads mail/calendar context; egress approval remains required. |
| Inbox Triage | Launches without approval; prepares drafts/research only; sending remains unavailable by default. |
| Calendar Planning | Foreground review required before proposed calendar mutations. |
| Meeting Prep | Launches without approval; reads calendar, mail, and Drive context; messaging/event changes remain gated. |
| Research Memo | Launches without approval; web/browser content is bound as external/public source context. |
| Web Research | Launches without approval; external content remains untrusted for later egress decisions. |
| Summarize Selection | Launches without approval; browser/frontmost-app/clipboard context is read-only. |
| Revise Frontmost Document | Foreground review required before document edits. |

If validation fails, treat it as product drift in the preset or workflow catalog.
The intended fix is to either add the missing bounded capability/source binding
or adjust the workflow declaration so it accurately describes the safe default
behavior.
