"""Guard: the shipped curated MCP catalog stays parseable and locked.

These configs let CapableDeputy drive real upstream MCP servers behind
the policy engine. The catalog's security guarantee rests on three
invariants this test pins:

  - every config parses (no CapabilityKind / Label typo silently
    dropping a tool override),
  - every server is strict (fail-closed admission), and
  - the community/preview Google Workspace config has an explicit
    override for every tool it declares (nothing destructive there may
    ride on inference).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from capabledeputy.upstream.config import load_config_file

_CURATED = Path(__file__).parent.parent / "configs" / "curated"
_FILES = sorted(_CURATED.glob("*.yaml"))


def test_curated_dir_is_present() -> None:
    assert _FILES, f"no curated configs found under {_CURATED}"


@pytest.mark.parametrize("path", _FILES, ids=lambda p: p.name)
def test_curated_config_parses_and_is_strict(path: Path) -> None:
    configs = load_config_file(path)
    assert configs, f"{path.name} parsed to zero servers"
    for c in configs:
        assert c.strict is True, f"{path.name}:{c.name} is not strict (fail-open)"
        assert c.command, f"{path.name}:{c.name} has empty command"


def test_google_workspace_pins_every_declared_tool() -> None:
    """Community/preview producer -> nothing may rely on inference."""
    configs = load_config_file(_CURATED / "google-workspace.yaml")
    for c in configs:
        assert c.tool_overrides, f"{c.name} declares no explicit tool overrides"
        for name, ov in c.tool_overrides.items():
            assert ov.capability_kind is not None, (
                f"{c.name}.{name} override has no capability_kind"
            )
