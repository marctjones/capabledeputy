"""JSON-RPC 2.0 message framing.

Messages are line-delimited JSON over a Unix socket: one JSON object per
newline-terminated line. Simpler than HTTP-style framing and adequate for
local IPC where both peers are trusted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

JSONRPC_VERSION = "2.0"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


@dataclass(frozen=True)
class RpcRequest:
    method: str
    params: dict[str, Any] = field(default_factory=dict)
    id: int | str | None = None
    jsonrpc: str = JSONRPC_VERSION

    def encode(self) -> bytes:
        payload: dict[str, Any] = {"jsonrpc": self.jsonrpc, "method": self.method}
        if self.params:
            payload["params"] = self.params
        if self.id is not None:
            payload["id"] = self.id
        return (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")


@dataclass(frozen=True)
class RpcResponse:
    id: int | str | None
    result: Any = None
    error: dict[str, Any] | None = None
    jsonrpc: str = JSONRPC_VERSION

    def encode(self) -> bytes:
        payload: dict[str, Any] = {"jsonrpc": self.jsonrpc, "id": self.id}
        if self.error is not None:
            payload["error"] = self.error
        else:
            payload["result"] = self.result
        return (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")


def parse_request(line: bytes) -> RpcRequest:
    obj = json.loads(line)
    if not isinstance(obj, dict):
        raise ValueError("expected JSON object")
    if "method" not in obj:
        raise ValueError("missing method")
    return RpcRequest(
        method=obj["method"],
        params=obj.get("params") or {},
        id=obj.get("id"),
        jsonrpc=obj.get("jsonrpc", JSONRPC_VERSION),
    )


def parse_response(line: bytes) -> RpcResponse:
    obj = json.loads(line)
    if not isinstance(obj, dict):
        raise ValueError("expected JSON object")
    return RpcResponse(
        id=obj.get("id"),
        result=obj.get("result"),
        error=obj.get("error"),
        jsonrpc=obj.get("jsonrpc", JSONRPC_VERSION),
    )


def error(code: int, message: str, data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return err
