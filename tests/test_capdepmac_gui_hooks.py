from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHAT_VIEW = ROOT / "apps/macos/CapDep/Sources/ChatView.swift"


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
