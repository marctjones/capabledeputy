"""Supervised daemon service: launchd / systemd unit generation + install (#318)."""

from capabledeputy.service.units import (
    DEFAULT_LABEL,
    daemon_program_args,
    launchd_plist,
    launchd_plist_path,
    systemd_unit,
    systemd_unit_path,
)

__all__ = [
    "DEFAULT_LABEL",
    "daemon_program_args",
    "launchd_plist",
    "launchd_plist_path",
    "systemd_unit",
    "systemd_unit_path",
]
