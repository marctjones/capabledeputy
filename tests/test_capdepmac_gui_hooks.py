import json
from pathlib import Path

from scripts import test_capdepmac_gui_interactions as gui_smoke

ROOT = Path(__file__).resolve().parents[1]
CHAT_VIEW = ROOT / "apps/macos/CapDep/Sources/ChatView.swift"
APP_MODEL = ROOT / "apps/macos/CapDep/Sources/CapDepAppModel.swift"
LAUNCHER = ROOT / "apps/macos/CapDep/scripts/run-local-app.sh"
GUI_SMOKE = ROOT / "scripts/test_capdepmac_gui_interactions.py"


def test_capdepmac_primary_chat_accessibility_hooks_are_stable() -> None:
    source = CHAT_VIEW.read_text(encoding="utf-8")

    for identifier in (
        "capdep.chat.window",
        "capdep.chat.composer",
        "capdep.chat.input",
        "capdep.chat.send",
        "capdep.chat.cancel-turn",
        "capdep.chat.connection-status",
        "capdep.chat.prompt-activity",
        "capdep.chat.message.user",
        "capdep.chat.message.assistant",
    ):
        assert f'"{identifier}"' in source


def test_capdepmac_no_focus_test_hook_is_opt_in() -> None:
    model_source = APP_MODEL.read_text(encoding="utf-8")
    launcher_source = LAUNCHER.read_text(encoding="utf-8")

    assert "CAPDEP_GUI_TEST_COMMAND_FILE" in model_source
    assert 'case "submit_prompt"' in model_source
    assert 'case "queue_prompt"' in model_source
    assert "gui_test_hook_submit_prompt" in model_source
    assert "gui_test_hook_queue_prompt" in model_source
    assert "output_has_image_markdown" in model_source
    assert "CAPDEP_GUI_TEST_COMMAND_FILE" in launcher_source
    assert "CAPDEP_GUI_BACKGROUND_OPEN" in launcher_source
    assert 'open -g -n "$APP"' in launcher_source


def test_capdepmac_gui_smoke_defaults_to_no_focus_driver() -> None:
    smoke_source = GUI_SMOKE.read_text(encoding="utf-8")

    assert 'choices=["test-hook", "keyboard"]' in smoke_source
    assert 'default="test-hook"' in smoke_source
    assert "--multi-prompt" in smoke_source
    assert "--no-wait-response" in smoke_source
    assert "--generated-image" in smoke_source
    assert "write_failure_artifacts" in smoke_source
    assert "CAPDEP_GUI_TEST_COMMAND_FILE" in smoke_source
    assert "CAPDEP_GUI_BACKGROUND_OPEN" in smoke_source


def test_capdepmac_gui_command_file_writes_jsonl(tmp_path: Path) -> None:
    command_file = tmp_path / "commands.jsonl"

    gui_smoke.write_test_hook_prompt(command_file, "hello from test")

    lines = command_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {
        "command": "submit_prompt",
        "message": "hello from test",
    }


def test_capdepmac_gui_command_file_can_queue_prompt(tmp_path: Path) -> None:
    command_file = tmp_path / "commands.jsonl"

    gui_smoke.write_test_hook_prompt(command_file, "queued from test", command="queue_prompt")

    assert json.loads(command_file.read_text(encoding="utf-8")) == {
        "command": "queue_prompt",
        "message": "queued from test",
    }


def test_capdepmac_gui_failure_artifacts_capture_trace_and_commands(
    tmp_path: Path,
    monkeypatch,
) -> None:
    command_file = tmp_path / "commands.jsonl"
    command_file.write_text('{"command":"queue_prompt","message":"hello"}\n', encoding="utf-8")
    trace_file = tmp_path / "chat-trace.log"
    trace_file.write_text("line 1\nline 2\n", encoding="utf-8")
    monkeypatch.setattr(gui_smoke, "CHAT_TRACE", trace_file)
    monkeypatch.setattr(gui_smoke, "run", lambda *args, **kwargs: "123 CapDepMac\n")

    args = type(
        "Args",
        (),
        {
            "driver": "test-hook",
            "messages": ["hello"],
            "wait_response": True,
            "generated_image": False,
            "require_ax_hooks": False,
        },
    )()

    artifact_dir = gui_smoke.write_failure_artifacts(
        artifact_dir=tmp_path / "artifacts",
        command_file=command_file,
        args=args,  # pyright: ignore[reportArgumentType]
        error=gui_smoke.SmokeError("boom"),
    )

    assert (artifact_dir / "error.txt").read_text(encoding="utf-8") == "boom"
    assert (artifact_dir / "command-file.jsonl").read_text(encoding="utf-8") == (
        '{"command":"queue_prompt","message":"hello"}\n'
    )
    assert "line 2" in (artifact_dir / "chat-trace-tail.log").read_text(encoding="utf-8")
    assert "CapDepMac" in (artifact_dir / "processes.txt").read_text(encoding="utf-8")
