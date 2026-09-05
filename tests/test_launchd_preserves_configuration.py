import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("command", ["start", "restart"])
def test_existing_operator_plist_survives_start_and_restart(tmp_path, command):
    root = Path(__file__).resolve().parents[1]
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy2(root / "scripts/run-local-daemon-launchd.sh", scripts)
    binary = tmp_path / ".venv/bin/capdep"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    launchctl = binary.parent / "launchctl"
    launchctl.write_text("#!/bin/sh\nexit 0\n")
    launchctl.chmod(0o755)
    plist = tmp_path / "operator.plist"
    original = b"operator-owned watchdog and model configuration\n"
    plist.write_bytes(original)
    env = {
        **os.environ,
        "CAPDEP_LAUNCHD_PLIST": str(plist),
        "PATH": str(binary.parent) + ":" + os.environ["PATH"],
    }
    subprocess.run(
        ["bash", str(scripts / "run-local-daemon-launchd.sh"), command],
        env=env,
        check=True,
        capture_output=True,
        timeout=10,
    )
    assert plist.read_bytes() == original
