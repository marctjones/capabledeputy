"""CI anti-rot guard for the narrated demos.

demos/scenarios/ is outside `testpaths`, so the demos don't run in the
default suite — which is how ~12 of them silently broke after the
label-model redesign (they imported the deleted `Label` / `AxisA` /
`AxisB` symbols). This guard import-checks every demo module so that class
of breakage fails CI immediately, and runs the whole narrated suite via
run_all so behavioral regressions are caught too.
"""

from __future__ import annotations

import importlib
import pkgutil

import demos.scenarios as _scenarios_pkg
import pytest

_DEMO_MODULES = sorted(
    name
    for _, name, _ in pkgutil.iter_modules(_scenarios_pkg.__path__)
    if not name.startswith("_")
)


@pytest.mark.parametrize("mod", _DEMO_MODULES)
def test_demo_module_imports(mod: str) -> None:
    """Every demo module imports cleanly (catches deleted-symbol rot)."""
    importlib.import_module(f"demos.scenarios.{mod}")


async def test_all_narrated_demos_run(tmp_path) -> None:
    """Run the whole narrated suite (25 demos) so behavior stays green."""
    from demos.scenarios.run_all import test_run_all_demos

    await test_run_all_demos(tmp_path)
