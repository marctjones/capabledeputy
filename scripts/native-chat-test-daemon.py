#!/usr/bin/env python3
"""Isolated synthetic streaming daemon; no model weights or external providers."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

import anyio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.daemon_integration import build_test_daemon, daemon_test_paths

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (ROOT / "tests/fixtures/commonmark/native-semantic.md").read_text()


class NativeUXModel:
    _model = "synthetic-native-chat"

    async def respond(self, messages, tools):
        raise NotImplementedError("The native fixture uses token streaming")

    async def respond_streaming(self, messages, tools, *, max_tokens=None):
        prompt = next(
            (message.content for message in reversed(messages) if message.role == "user"), ""
        )
        if "[stream]" in prompt:
            yield "Streaming started.\n\n"
            for index in range(80):
                await anyio.sleep(0.25)
                yield f"Streaming paragraph {index}: reading position should stay stable.\n\n"
            yield "Streaming finished."
        elif "[long]" in prompt:
            yield (
                "Long transcript.\n\n"
                + "\n\n".join(
                    f"Paragraph {index}: synthetic history for native "
                    "reading-position verification. " * 3
                    for index in range(100)
                )
                + "\n\nLong transcript finished."
            )
        else:
            yield "Native fixture follows.\n\n"
            for start in range(0, len(FIXTURE), 100):
                await anyio.sleep(0.05)
                yield FIXTURE[start : start + 100]


async def run(socket_path, state_dir):
    if socket_path.exists():
        raise FileExistsError("Refusing to replace an existing socket; use a fresh isolated path")
    state_dir.mkdir(parents=True, exist_ok=True)
    paths = replace(daemon_test_paths(state_dir), socket=socket_path)
    daemon, app = await build_test_daemon(paths)
    app.llm_client = NativeUXModel()
    app.model_pool = None
    print(
        json.dumps({"synthetic": True, "model_weights": False, "socket": str(socket_path)}),
        flush=True,
    )
    try:
        await daemon.serve()
    finally:
        await app.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    anyio.run(run, args.socket, args.state_dir)
