"""Daemon-owned operator settings.

These settings are product/UI preferences, not policy decisions. The daemon
owns persistence so every client surface reads and writes the same state.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from capabledeputy.cli._managed_config import user_config_dir


@dataclass(frozen=True)
class DaemonSettings:
    default_purpose: str = "general"
    global_shortcut: str = "Option-Space"
    image_profile: str = "default"
    launch_at_login: bool = False
    notifications_enabled: bool = True
    prefer_local_mlx: bool = True
    show_thinking_output: bool = False
    enable_screen_control: bool = False
    require_touch_id_for_high_risk: bool = False
    verbose_daemon_logging: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_FIELDS = set(DaemonSettings.__dataclass_fields__)
_STRING_FIELDS = {"default_purpose", "global_shortcut", "image_profile"}
_BOOL_FIELDS = _FIELDS - _STRING_FIELDS


def default_settings_path() -> Path:
    return user_config_dir() / "settings.json"


def load_settings(path: Path | None = None) -> DaemonSettings:
    settings_path = path or default_settings_path()
    if not settings_path.is_file():
        return DaemonSettings()
    raw = json.loads(settings_path.read_text(encoding="utf-8") or "{}")
    if not isinstance(raw, dict):
        raise ValueError("settings file must contain a JSON object")
    return _coerce_settings(raw, base=DaemonSettings())


def save_settings(settings: DaemonSettings, path: Path | None = None) -> None:
    settings_path = path or default_settings_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(settings.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    settings_path.chmod(0o600)


def update_settings(
    updates: dict[str, Any],
    *,
    path: Path | None = None,
) -> tuple[DaemonSettings, tuple[str, ...]]:
    current = load_settings(path)
    changed: list[str] = []
    clean: dict[str, Any] = {}
    for key, value in updates.items():
        if key not in _FIELDS:
            raise ValueError(f"unknown setting: {key}")
        clean[key] = _coerce_value(key, value)
    for key, value in clean.items():
        if getattr(current, key) != value:
            changed.append(key)
    updated = replace(current, **clean)
    save_settings(updated, path)
    return updated, tuple(changed)


def _coerce_settings(raw: dict[str, Any], *, base: DaemonSettings) -> DaemonSettings:
    clean = {key: _coerce_value(key, value) for key, value in raw.items() if key in _FIELDS}
    return replace(base, **clean)


def _coerce_value(key: str, value: Any) -> Any:
    if key == "default_purpose":
        purpose = str(value).strip()
        if not purpose:
            raise ValueError("default_purpose must be non-empty")
        return purpose
    if key == "global_shortcut":
        shortcut = str(value).strip()
        if not shortcut:
            raise ValueError("global_shortcut must be non-empty")
        return shortcut
    if key == "image_profile":
        profile = str(value).strip().lower()
        if not profile:
            raise ValueError("image_profile must be non-empty")
        return profile
    if key in _BOOL_FIELDS:
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be boolean")
        return value
    raise ValueError(f"unknown setting: {key}")
