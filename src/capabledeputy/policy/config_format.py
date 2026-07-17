"""#384 — format-agnostic config resolution.

`docs/policy-authoring-design.md` §7: one format for hand-edited policy. YAML is
a superset of JSON, so a single `yaml.safe_load` reads both — legacy `.json`
files keep loading while operators migrate to `.yaml`. `resolve_config_path`
lets a file be renamed `.json`↔`.yaml` without touching any call site: when the
requested path is absent, its sibling with the other extension is used.
"""

from __future__ import annotations

from pathlib import Path


def resolve_config_path(path: Path) -> Path:
    """Return `path` if it exists; otherwise its `.json`↔`.yaml` sibling if THAT
    exists; otherwise `path` unchanged (so the caller's missing-file error still
    names the path it asked for)."""
    if path.is_file():
        return path
    if path.suffix == ".json":
        alt = path.with_suffix(".yaml")
    elif path.suffix in (".yaml", ".yml"):
        alt = path.with_suffix(".json")
    else:
        return path
    return alt if alt.is_file() else path
