#!/usr/bin/env python3
"""Manual GUI cancellation harness. No model weights; temporary application state.

Run: .venv/bin/python -m tests.manual_gui_fake
The normal daemon must be stopped.
Send '[slow]' to wait for cancellation; other messages receive a fixed reply.
"""

from __future__ import annotations

import argparse
import json
import socket
import tempfile
from dataclasses import replace
from pathlib import Path

import anyio

from capabledeputy.ipc.socket_path import default_socket_path
from tests.daemon_integration import build_test_daemon, daemon_test_paths


class SlowFakeModel:
    _model = "scripted-gui-test"

    async def respond_streaming(self, messages, tools, *, max_tokens=None):
        prompt = next((m.content for m in reversed(messages) if m.role == "user"), "")
        if "[slow]" in prompt:
            print(json.dumps({"event": "fake_stream_started"}), flush=True)
            try:
                yield "Scripted response waiting for Stop."
                await anyio.sleep(120)
                yield " Stop was not received."
            finally:
                print(json.dumps({"event": "fake_stream_closed"}), flush=True)
        else:
            yield "Scripted recovery response: ready."


async def run(path: Path) -> None:
    if path.exists():
        with socket.socket(socket.AF_UNIX) as probe:
            try:
                probe.connect(str(path))
            except ConnectionRefusedError:
                pass  # Stale socket; Daemon.serve handles replacement.
            else:
                raise RuntimeError("A daemon already owns this socket; stop it first.")
    with tempfile.TemporaryDirectory(prefix="capdep-fake-gui-") as folder:
        paths = replace(daemon_test_paths(Path(folder)), socket=path)
        daemon, app = await build_test_daemon(paths)
        app.llm_client = SlowFakeModel()
        app.model_pool = None
        print(
            json.dumps({"event": "fake_gui_ready", "socket": str(path), "state": folder}),
            flush=True,
        )
        await daemon.serve()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", type=Path, default=default_socket_path())
    anyio.run(run, parser.parse_args().socket)
