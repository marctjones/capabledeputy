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
    assert "gui_test_hook_submit_prompt" in model_source
    assert "CAPDEP_GUI_TEST_COMMAND_FILE" in launcher_source
    assert "CAPDEP_GUI_BACKGROUND_OPEN" in launcher_source
    assert 'open -g -n "$APP"' in launcher_source


def test_capdepmac_gui_smoke_defaults_to_no_focus_driver() -> None:
    smoke_source = GUI_SMOKE.read_text(encoding="utf-8")

    assert 'choices=["test-hook", "keyboard"]' in smoke_source
    assert 'default="test-hook"' in smoke_source
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
