"""Image generation runs in the isolated `.venv-images` (mflux/mlx live there),
not in the daemon's `.venv`. Covers the one-shot worker and the daemon handler's
spawn-vs-in-process resolution + faithful failure surfacing.
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest

from capabledeputy.daemon import image_ops_handlers as ioh
from capabledeputy.mcp_servers import image_gen_worker


def test_worker_main_runs_pipeline_and_emits_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from capabledeputy.mcp_servers import _image_pipeline

    monkeypatch.setattr(_image_pipeline, "load_image_gen_config", lambda profile_name=None: object)
    captured: dict = {}

    def fake_generate(**kwargs: object) -> dict:
        captured.update(kwargs)
        return {"ok": True, "image_path": "/x.png"}

    monkeypatch.setattr(_image_pipeline, "generate_image", fake_generate)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"prompt": "a cat", "steps": 2})))

    image_gen_worker.main()

    out = json.loads(capsys.readouterr().out)
    assert out == {"ok": True, "image_path": "/x.png"}
    assert captured["prompt"] == "a cat"
    assert captured["steps"] == 2


def _run(**over: object) -> dict:
    kwargs: dict = {
        "prompt": "p",
        "style": None,
        "negative_prompt": None,
        "width": None,
        "height": None,
        "steps": None,
        "seed": None,
        "alt": None,
        "filename": None,
        "profile": None,
    }
    kwargs.update(over)
    return ioh._run_image_generation(**kwargs)  # type: ignore[arg-type]


def test_run_generation_falls_back_in_process_without_venv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ioh, "_image_runtime_python", lambda: None)
    monkeypatch.setattr(ioh, "load_image_gen_config", lambda profile_name=None: object())
    called: dict = {}

    def fake_generate(**kwargs: object) -> dict:
        called.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(ioh, "generate_image", fake_generate)
    assert _run(profile="default") == {"ok": True}
    assert called["prompt"] == "p"


def test_run_generation_spawns_worker_and_parses_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ioh, "_image_runtime_python", lambda: Path("/imgpy"))

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        assert cmd[0] == "/imgpy"
        assert cmd[1:] == ["-m", "capabledeputy.mcp_servers.image_gen_worker"]
        stdout = '{"ok": true, "image_path": "/y.png"}'
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(ioh.subprocess, "run", fake_run)
    assert _run() == {"ok": True, "image_path": "/y.png"}


def test_run_generation_worker_failure_is_faithful(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ioh, "_image_runtime_python", lambda: Path("/imgpy"))

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="No module named 'mflux'")

    monkeypatch.setattr(ioh.subprocess, "run", fake_run)
    out = _run()
    assert out["ok"] is False
    assert "No module named 'mflux'" in out["error"]


def test_run_generation_spawn_exception_is_faithful(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ioh, "_image_runtime_python", lambda: Path("/imgpy"))

    def boom(*a, **k):  # type: ignore[no-untyped-def]
        raise OSError("spawn kaboom")

    monkeypatch.setattr(ioh.subprocess, "run", boom)
    out = _run()
    assert out["ok"] is False
    assert "spawn failed" in out["error"]


def test_run_generation_invalid_worker_output_is_faithful(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ioh, "_image_runtime_python", lambda: Path("/imgpy"))

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        return subprocess.CompletedProcess(cmd, 0, stdout="not json", stderr="")

    monkeypatch.setattr(ioh.subprocess, "run", fake_run)
    out = _run()
    assert out["ok"] is False
    assert "invalid output" in out["error"]
