from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKLIST = ROOT / "docs/test-worklist.md"


def test_test_worklist_covers_required_tiers_and_boundaries() -> None:
    text = WORKLIST.read_text(encoding="utf-8")

    for required in (
        "Standard Deterministic Suite",
        "Opt-In Real AI Smokes",
        "Data Rules",
        "CAPDEP_GUI_TEST_COMMAND_FILE",
        "Daemon image jobs",
        "Real MCP plus AI smoke",
        "Real image smoke",
        "Default tests use test data only",
    ):
        assert required in text
