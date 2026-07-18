"""#322 — unified health check backing `capdep doctor`.

Health checks were scattered across `status` / config-doctor / setup-check /
validate-daemon, and none tested runtime health. This is the single place that
runs them all: daemon liveness, config validity, state-DB integrity, and the
LLM key. Each check degrades gracefully (a down daemon or absent file is a
reported result, never a crash), and `overall_status` collapses them to an exit
code.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Status = Literal["ok", "warn", "fail"]


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str


def check_configs(configs_dir: Path | None = None) -> Check:
    """v0.9 config files present + parseable, and (if a unified capdep.yaml
    exists) it passes the policy-check gate."""
    from capabledeputy.daemon.lifecycle import (
        V09ConfigError,
        _resolve_v09_configs_dir,
        load_v09_configs,
    )

    base = _resolve_v09_configs_dir(configs_dir)
    try:
        load_v09_configs(base)
    except V09ConfigError as e:
        return Check("configs", "fail", str(e))

    capdep = base / "capdep.yaml"
    if capdep.is_file():
        from capabledeputy.policy.authoring import ConfigError, load_config
        from capabledeputy.policy.policy_check import check_policy, has_errors

        try:
            problems = check_policy(load_config(capdep))
        except ConfigError as e:
            return Check("configs", "fail", f"capdep.yaml: {e}")
        if has_errors(problems):
            errs = "; ".join(p.message for p in problems if p.severity == "error")
            return Check("configs", "fail", f"capdep.yaml policy check: {errs}")
        if problems:
            return Check("configs", "warn", f"{len(problems)} policy-check warning(s)")
    return Check("configs", "ok", f"config tree valid at {base}")


def check_state_db(db_path: Path | None = None) -> Check:
    """SQLite integrity of the state DB. Absent DB is fine (fresh install)."""
    from capabledeputy.paths import default_state_db_path

    path = db_path or default_state_db_path()
    if not path.is_file():
        return Check("state-db", "ok", f"no state DB yet at {path} (fresh install)")
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            result = con.execute("PRAGMA integrity_check").fetchone()
        finally:
            con.close()
    except sqlite3.Error as e:
        return Check("state-db", "fail", f"cannot open {path}: {e}")
    if result and result[0] == "ok":
        return Check("state-db", "ok", f"integrity ok ({path})")
    return Check("state-db", "fail", f"integrity check failed: {result}")


def check_llm_key() -> Check:
    """An LLM API key is resolvable (env or file fallback)."""
    from capabledeputy.secrets import load_anthropic_api_key

    return (
        Check("llm-key", "ok", "API key resolved")
        if load_anthropic_api_key()
        else Check(
            "llm-key", "warn", "no API key found (set ANTHROPIC_API_KEY or use a local model)"
        )
    )


async def check_daemon(socket_path: Path | None = None) -> Check:
    """Daemon liveness via a ping RPC. Not running is a WARN (a fresh box), a
    connected-but-erroring daemon is a FAIL."""
    from capabledeputy.ipc.client import DaemonClient, DaemonNotRunningError
    from capabledeputy.ipc.socket_path import default_socket_path

    sp = socket_path or default_socket_path()
    try:
        await DaemonClient(sp).call("ping")
    except (DaemonNotRunningError, ConnectionError, FileNotFoundError, OSError):
        # Any connection-level failure = the daemon isn't reachable. A fresh box
        # with no daemon is a WARN, not a hard failure.
        return Check("daemon", "warn", f"not running at {sp}")
    except Exception as e:  # connected but the ping itself errored — a real fault
        return Check("daemon", "fail", f"daemon at {sp} did not answer ping: {e}")
    return Check("daemon", "ok", f"responsive at {sp}")


async def run_all(
    *,
    configs_dir: Path | None = None,
    db_path: Path | None = None,
    socket_path: Path | None = None,
) -> list[Check]:
    return [
        await check_daemon(socket_path),
        check_configs(configs_dir),
        check_state_db(db_path),
        check_llm_key(),
    ]


def overall_status(checks: list[Check]) -> Status:
    if any(c.status == "fail" for c in checks):
        return "fail"
    if any(c.status == "warn" for c in checks):
        return "warn"
    return "ok"
