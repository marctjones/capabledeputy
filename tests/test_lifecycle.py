from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import anyio
import pytest

from capabledeputy.daemon.lifecycle import (
    DEFAULT_AGENT_MAX_ITERATIONS,
    V09ConfigError,
    _read_daemon_config,
    _report_admission,
    _report_runtime_manifest,
    _resolve_v09_configs_dir,
    agent_max_iterations,
    daemon_status,
    idle_shutdown_seconds,
    load_v09_configs,
    max_delegation_depth,
    run_daemon,
    stop_daemon,
)
from capabledeputy.policy.capabilities import DEFAULT_MAX_DELEGATION_DEPTH
from tests._socket_helpers import short_socket_path


async def _wait_for_socket(path: Path, timeout: float = 15.0) -> None:
    deadline = anyio.current_time() + timeout
    while anyio.current_time() < deadline:
        if path.exists():
            try:
                stream = await anyio.connect_unix(str(path))
                await stream.aclose()
                return
            except (FileNotFoundError, ConnectionRefusedError):
                pass
        await anyio.sleep(0.01)
    raise TimeoutError(f"socket {path} did not become available within {timeout}s")


async def test_status_reports_not_running_when_no_daemon(tmp_path: Path) -> None:
    socket_path = short_socket_path("no-daemon.sock")
    status = await daemon_status(socket_path)
    # Issue #1 broadened daemon_status to also report the pid from
    # the pidfile (None when no daemon is running). Don't pin the
    # whole dict shape; just the running flag.
    assert status["running"] is False


async def test_stop_returns_false_when_no_daemon(tmp_path: Path) -> None:
    socket_path = short_socket_path("no-daemon.sock")
    assert await stop_daemon(socket_path) is False


def test_idle_shutdown_seconds_defaults_to_one_minute(monkeypatch) -> None:
    monkeypatch.delenv("CAPDEP_IDLE_SHUTDOWN_SECONDS", raising=False)

    assert idle_shutdown_seconds() == 60.0


def test_idle_shutdown_seconds_can_be_disabled(monkeypatch) -> None:
    monkeypatch.setenv("CAPDEP_IDLE_SHUTDOWN_SECONDS", "off")

    assert idle_shutdown_seconds() is None


def test_env_tunable_lifecycle_limits_fail_safe(monkeypatch) -> None:
    monkeypatch.delenv("CAPDEP_MAX_DELEGATION_DEPTH", raising=False)
    assert max_delegation_depth() == DEFAULT_MAX_DELEGATION_DEPTH
    monkeypatch.setenv("CAPDEP_MAX_DELEGATION_DEPTH", "9")
    assert max_delegation_depth() == 9
    monkeypatch.setenv("CAPDEP_MAX_DELEGATION_DEPTH", "bad")
    assert max_delegation_depth() == DEFAULT_MAX_DELEGATION_DEPTH
    monkeypatch.setenv("CAPDEP_MAX_DELEGATION_DEPTH", "-1")
    assert max_delegation_depth() == DEFAULT_MAX_DELEGATION_DEPTH

    monkeypatch.delenv("CAPDEP_AGENT_MAX_ITERATIONS", raising=False)
    assert agent_max_iterations() == DEFAULT_AGENT_MAX_ITERATIONS
    monkeypatch.setenv("CAPDEP_AGENT_MAX_ITERATIONS", "7")
    assert agent_max_iterations() == 7
    monkeypatch.setenv("CAPDEP_AGENT_MAX_ITERATIONS", "bad")
    assert agent_max_iterations() == DEFAULT_AGENT_MAX_ITERATIONS
    monkeypatch.setenv("CAPDEP_AGENT_MAX_ITERATIONS", "0")
    assert agent_max_iterations() == DEFAULT_AGENT_MAX_ITERATIONS

    monkeypatch.setenv("CAPDEP_IDLE_SHUTDOWN_SECONDS", "2.5")
    assert idle_shutdown_seconds() == 2.5
    monkeypatch.setenv("CAPDEP_IDLE_SHUTDOWN_SECONDS", "bad")
    assert idle_shutdown_seconds() == 60.0
    monkeypatch.setenv("CAPDEP_IDLE_SHUTDOWN_SECONDS", "-1")
    assert idle_shutdown_seconds() is None


def test_v09_config_resolution_and_fail_closed_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    explicit = tmp_path / "explicit"
    assert _resolve_v09_configs_dir(explicit) == explicit

    env_dir = tmp_path / "env"
    monkeypatch.setenv("CAPDEP_CONFIGS_DIR", str(env_dir))
    assert _resolve_v09_configs_dir() == env_dir

    missing_dir = tmp_path / "missing"
    with pytest.raises(V09ConfigError, match="configs dir not found"):
        load_v09_configs(missing_dir)

    partial = tmp_path / "partial"
    partial.mkdir()
    with pytest.raises(V09ConfigError, match="config missing"):
        load_v09_configs(partial)

    malformed_json = tmp_path / "malformed-json"
    malformed_json.mkdir()
    (malformed_json / "risk_register.json").write_text("{", encoding="utf-8")
    with pytest.raises(V09ConfigError, match="config unparseable"):
        load_v09_configs(malformed_json)


def test_v09_config_yaml_parse_errors_fail_closed(tmp_path: Path) -> None:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    for name in ("risk_register.json", "risk_preference.json"):
        (config_dir / name).write_text("{}", encoding="utf-8")
    for name in (
        "purposes.yaml",
        "source_bindings.yaml",
        "relationship_groups.yaml",
        "expectations.yaml",
        "override_policy.yaml",
        "envelopes.yaml",
        "labels.yaml",
        "profiles.yaml",
    ):
        (config_dir / name).write_text("{}\n", encoding="utf-8")
    (config_dir / "rules.yaml").write_text("bad: [", encoding="utf-8")

    with pytest.raises(V09ConfigError, match="config unparseable"):
        load_v09_configs(config_dir)


def test_read_daemon_config_handles_absent_yaml_json_and_non_dict(tmp_path: Path) -> None:
    assert _read_daemon_config(tmp_path / "absent.yaml") == {}

    yaml_config = tmp_path / "daemon.yaml"
    yaml_config.write_text("upstream_servers: []\n", encoding="utf-8")
    assert _read_daemon_config(yaml_config) == {"upstream_servers": []}

    json_config = tmp_path / "daemon.json"
    json_config.write_text('{"policy_preview": false}\n', encoding="utf-8")
    assert _read_daemon_config(json_config) == {"policy_preview": False}

    list_config = tmp_path / "daemon-list.json"
    list_config.write_text("[]\n", encoding="utf-8")
    assert _read_daemon_config(list_config) == {}


def test_runtime_reports_include_refusals_and_manifest_errors(capsys) -> None:
    manager = SimpleNamespace(
        adapters=[
            SimpleNamespace(name="clean", rejected_tools=[]),
            SimpleNamespace(name="strict", rejected_tools=["unsafe.send"]),
        ],
    )
    _report_admission(cast(Any, manager))
    stderr = capsys.readouterr().err
    assert "[upstream] clean: registered ok" in stderr
    assert "REFUSED 1 unclassified: ['unsafe.send']" in stderr

    issue = SimpleNamespace(subject="upstream_servers.bad", message="missing kind")
    manifest = SimpleNamespace(
        validate=lambda: SimpleNamespace(warnings=["warn"], errors=[issue]),
        summary=lambda: {"tools": 3, "upstream_servers": 1, "hooks": 2},
    )
    with pytest.raises(RuntimeError, match="runtime manifest validation failed"):
        _report_runtime_manifest(cast(Any, manifest))
    stderr = capsys.readouterr().err
    assert "[manifest] tools=3 upstream_servers=1 hooks=2 warnings=1" in stderr
    assert "[manifest] ERROR upstream_servers.bad: missing kind" in stderr


async def test_run_status_stop_lifecycle(tmp_path: Path) -> None:
    socket_path = short_socket_path("lifecycle.sock")

    async with anyio.create_task_group() as tg:
        # Pin the state DB + audit log to tmp_path: the default paths are
        # under $XDG_STATE_HOME and may be unwritable / shared in CI
        # ("unable to open database file"); isolating them also deflakes.
        tg.start_soon(
            run_daemon,
            socket_path,
            tmp_path / "state.db",
            tmp_path / "audit.jsonl",
        )
        await _wait_for_socket(socket_path)

        status = await daemon_status(socket_path)
        assert status["running"] is True
        assert status["ping"] == {"ok": True}

        assert await stop_daemon(socket_path) is True
