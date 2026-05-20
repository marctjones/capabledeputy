"""End-to-end scenarios that exercise CapableDeputy's security model
against the kinds of work people use OpenClaw and similar personal AI
agents for. Each module is a runnable pytest test + an operator-facing
narrative (visible with `pytest -s`).

Pattern: every scenario constructs its own `App` with a tailored
`PolicyContext`, drives a workflow through `app.tool_client.call_tool`,
and asserts the security promise via decision outcomes + audit-event
sequences.
"""
