"""RPC handlers for the standalone quarantined-extraction REPL command.

`extract.inbox_message` runs the quarantined LLM against the body of
an inbox message directly. No session is involved; the result is a
schema-validated dict that the user can act on from a clean session.

This is the REPL-driven counterpart to the `quarantined.extract` tool
(which operates on memory keys). The two share `quarantined.extractor.extract`
under the hood.
"""

from __future__ import annotations

from typing import Any

from capabledeputy.app import App
from capabledeputy.daemon.handlers import Handler
from capabledeputy.quarantined.extractor import ExtractionError, extract
from capabledeputy.quarantined.schemas import list_schemas


def make_extract_handlers(app: App) -> dict[str, Handler]:
    async def schemas(params: dict[str, Any]) -> dict[str, Any]:
        return {"schemas": list_schemas()}

    async def inbox_ids(params: dict[str, Any]) -> dict[str, Any]:
        """Read-only listing of inbox messages (id + sender + subject)
        for the REPL TAB completer. Intentionally lighter than the
        agent's `inbox.list` tool — this is plumbing for the REPL UI,
        not a session-tainted read."""
        return {
            "messages": [
                {"id": m.id, "sender": m.sender, "subject": m.subject} for m in app.inbox.all()
            ],
        }

    async def inbox_message(params: dict[str, Any]) -> dict[str, Any]:
        if app.quarantined_llm is None:
            return {
                "error": "no quarantined LLM configured. Set "
                "CAPDEP_QUARANTINED_LLM_MODEL before starting the daemon.",
            }
        message_id = str(params["message_id"])
        schema_name = str(params["schema"])

        message = app.inbox.get(message_id)
        if message is None:
            return {"error": f"no inbox message with id: {message_id}"}
        if schema_name not in list_schemas():
            return {
                "error": (f"unknown schema: {schema_name}. available: {', '.join(list_schemas())}"),
            }

        try:
            result = await extract(app.quarantined_llm, schema_name, message.body)
        except ExtractionError as e:
            return {"error": str(e), "message_id": message_id, "schema": schema_name}
        return {
            "message_id": message_id,
            "schema": schema_name,
            "data": result.model_dump(mode="json"),
        }

    return {
        "extract.schemas": schemas,
        "extract.inbox_message": inbox_message,
        "extract.inbox_ids": inbox_ids,
    }
