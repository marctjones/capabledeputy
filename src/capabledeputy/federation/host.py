"""HostId — stable per-install identifier for federation.

The host id lives at `$XDG_DATA_HOME/capabledeputy/host_id`. It is a
random 16-byte hex string the install picks once and reuses forever.
Audit events tagging a foreign-originated record use this id as the
attribution string so a household with phone + laptop produces a
clear cross-host trace.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HostId:
    value: str
    display_name: str = ""

    def __str__(self) -> str:
        return f"host:{self.value}"


def load_or_create_host_id(path: Path, *, display_name: str = "") -> HostId:
    if path.exists():
        return HostId(value=path.read_text(encoding="utf-8").strip(), display_name=display_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_hex(16)
    path.write_text(value, encoding="utf-8")
    return HostId(value=value, display_name=display_name)
