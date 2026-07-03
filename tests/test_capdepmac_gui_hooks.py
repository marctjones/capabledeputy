from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHAT_VIEW = ROOT / "apps/macos/CapDep/Sources/ChatView.swift"
APP_MODEL = ROOT / "apps/macos/CapDep/Sources/CapDepAppModel.swift"
LAUNCHER = ROOT / "apps/macos/CapDep/scripts/run-local-app.sh"


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
