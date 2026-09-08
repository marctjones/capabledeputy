import importlib.util
import os
import sys
from pathlib import Path

import pytest


def load_guard():
    spec = importlib.util.spec_from_file_location(
        "guard", Path(__file__).resolve().parents[1] / "scripts/guard-daemon-resources.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("failure", ["pressure", "telemetry"])
def test_guard_terminates_owned_child(monkeypatch, failure):
    guard = load_guard()
    pids = []

    def sample(pid):
        pids.append(pid)
        if failure == "telemetry":
            raise RuntimeError("telemetry unavailable")
        return {"rss_mib": 50, "cpu_percent": 0, "pressure": 4}

    monkeypatch.setattr(guard, "sample", sample)
    monkeypatch.setattr(guard.signal, "signal", lambda *args: None)
    monkeypatch.setattr(sys, "argv", ["guard", sys.executable, "-c", "import time; time.sleep(60)"])
    assert guard.main() == 0
    assert pids
    with pytest.raises(ProcessLookupError):
        os.kill(pids[0], 0)


def test_guard_limits_and_short_cpu_bursts():
    guard = load_guard()
    normal = {"rss_mib": 500, "cpu_percent": 300, "pressure": 1}
    assert guard.reason_for(normal, 0, 10) is None
    assert guard.reason_for(normal, 0, 30)
    assert guard.reason_for(normal, 15, 0)
    assert guard.reason_for({**normal, "rss_mib": 2048}, 0, 0)
