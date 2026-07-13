"""Parse SKILL.md files into structured Skill records.

A skill file is YAML frontmatter (delimited by `---` lines) followed by
a prompt body. Required frontmatter fields: `name`, `description`.
Optional fields:

  - `capability_kind`: one of the registered CapabilityKind values.
    Default READ_FS.
  - `inherent_tags`: dict with keys "a" and "b" for Axis-A and Axis-B
    tags that the skill's output carries (four-axis LabelState format).
  - `parameters`: JSON schema (dict) for the tool's call args. Default
    is `{"type": "object"}`.
  - `target_arg`: which arg holds the policy target string. Default
    `"target"`.
  - `schema`: name of a registered declassification schema. When set,
    the skill runs through the structured-extraction path and returns
    a Pydantic-validated object instead of free text.

The body — everything after the frontmatter close `---` — is the prompt
template. `{{var}}` is replaced with the matching call arg's value at
invocation; missing vars become an empty string.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.labels import LabelState


class SkillParseError(ValueError):
    pass


class SkillMode(StrEnum):
    GUIDANCE = "guidance"
    TOOL = "tool"
    HYBRID = "hybrid"


@dataclass(frozen=True)
class SkillResource:
    kind: str
    relpath: str
    size: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "relpath": self.relpath,
            "size": self.size,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class SkillScript:
    relpath: str
    language: str
    spec_id: str | None = None
    timeout_seconds: int = 30

    def to_dict(self) -> dict[str, Any]:
        return {
            "relpath": self.relpath,
            "language": self.language,
            "spec_id": self.spec_id,
            "timeout_seconds": self.timeout_seconds,
        }


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_RESOURCE_DIRS = {"references", "scripts", "assets", "agents"}


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    mode: SkillMode = SkillMode.TOOL
    capability_kind: CapabilityKind = CapabilityKind.READ_FS
    inherent_tags: LabelState = field(default_factory=LabelState)
    parameters_schema: dict[str, Any] = field(
        default_factory=lambda: {"type": "object", "properties": {}, "required": []},
    )
    target_arg: str = "target"
    schema_name: str | None = None
    body: str = ""
    source_path: Path | None = None
    package_root: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    resources: tuple[SkillResource, ...] = field(default_factory=tuple)
    scripts: tuple[SkillScript, ...] = field(default_factory=tuple)
    diagnostics: tuple[str, ...] = field(default_factory=tuple)

    def render(self, args: dict[str, Any]) -> str:
        """Substitute `{{var}}` placeholders in the body with arg values."""

        def _sub(match: re.Match[str]) -> str:
            return str(args.get(match.group(1), ""))

        return _PLACEHOLDER_RE.sub(_sub, self.body)

    @property
    def tool_enabled(self) -> bool:
        return self.mode in {SkillMode.TOOL, SkillMode.HYBRID}

    @property
    def guidance_enabled(self) -> bool:
        return self.mode in {SkillMode.GUIDANCE, SkillMode.HYBRID}

    def to_summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "mode": self.mode.value,
            "capability_kind": self.capability_kind.value,
            "source_path": str(self.source_path) if self.source_path else None,
            "package_root": str(self.package_root) if self.package_root else None,
            "tool_enabled": self.tool_enabled,
            "guidance_enabled": self.guidance_enabled,
            "resources": [resource.to_dict() for resource in self.resources],
            "scripts": [script.to_dict() for script in self.scripts],
            "diagnostics": list(self.diagnostics),
            "metadata": self.metadata,
        }


def parse_skill_text(
    text: str,
    *,
    source_path: Path | None = None,
    package_root: Path | None = None,
    default_mode: SkillMode = SkillMode.TOOL,
) -> Skill:
    """Parse a single SKILL.md file's contents into a Skill record."""
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        raise SkillParseError(
            "skill file must start with YAML frontmatter delimited by '---' lines",
        )
    front_text, body = match.group(1), match.group(2)

    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as e:
        raise SkillParseError(
            "PyYAML is required for skill files; install with `uv add pyyaml`",
        ) from e

    front = yaml.safe_load(front_text) or {}
    if not isinstance(front, dict):
        raise SkillParseError(f"skill frontmatter must be a YAML mapping, got {type(front)}")

    name = front.get("name")
    description = front.get("description")
    if not name or not isinstance(name, str):
        raise SkillParseError("skill frontmatter missing required field: name")
    if not description or not isinstance(description, str):
        raise SkillParseError("skill frontmatter missing required field: description")

    try:
        mode = SkillMode(str(front.get("mode") or default_mode.value))
    except ValueError as e:
        raise SkillParseError(
            "skill mode must be one of: guidance, tool, hybrid",
        ) from e

    kind_str = front.get("capability_kind") or CapabilityKind.READ_FS.value
    try:
        capability_kind = CapabilityKind(kind_str)
    except ValueError as e:
        raise SkillParseError(f"unknown capability_kind: {kind_str}") from e

    tags_raw = front.get("inherent_tags") or {}
    try:
        inherent_tags = LabelState.from_dict(tags_raw)
    except (ValueError, KeyError, TypeError) as e:
        raise SkillParseError(f"invalid tags: {e}") from e

    params = front.get("parameters") or {"type": "object", "properties": {}, "required": []}
    if not isinstance(params, dict):
        raise SkillParseError("parameters must be a JSON schema object")

    target_arg = front.get("target_arg", "target")
    if not isinstance(target_arg, str):
        raise SkillParseError("target_arg must be a string")

    schema_name = front.get("schema")
    if schema_name is not None and not isinstance(schema_name, str):
        raise SkillParseError("schema must be a string when set")

    metadata = front.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise SkillParseError("metadata must be a mapping when set")

    scripts = _parse_scripts(front.get("scripts") or front.get("script"))
    diagnostics: list[str] = []
    if mode in {SkillMode.GUIDANCE, SkillMode.HYBRID}:
        diagnostics.append(
            "guidance is treated as untrusted package content, not operator authority",
        )
    if scripts:
        diagnostics.append("scripts require sandbox/container execution; host execution is refused")

    return Skill(
        name=name,
        description=description,
        mode=mode,
        capability_kind=capability_kind,
        inherent_tags=inherent_tags,
        parameters_schema=params,
        target_arg=target_arg,
        schema_name=schema_name,
        body=body,
        source_path=source_path,
        package_root=package_root,
        metadata=dict(metadata),
        resources=_discover_resources(package_root),
        scripts=scripts,
        diagnostics=tuple(diagnostics),
    )


def parse_skill_file(path: Path) -> Skill:
    return parse_skill_text(path.read_text(encoding="utf-8"), source_path=path)


def parse_skill_package(path: Path) -> Skill:
    skill_path = path / "SKILL.md" if path.is_dir() else path
    if not skill_path.is_file():
        raise SkillParseError(f"skill package missing SKILL.md: {path}")
    return parse_skill_text(
        skill_path.read_text(encoding="utf-8"),
        source_path=skill_path,
        package_root=skill_path.parent,
        default_mode=SkillMode.GUIDANCE,
    )


def _parse_scripts(raw: Any) -> tuple[SkillScript, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        return (_script_from_mapping({"path": raw}),)
    if isinstance(raw, dict):
        return (_script_from_mapping(raw),)
    if isinstance(raw, list):
        return tuple(_script_from_mapping(item) for item in raw)
    raise SkillParseError("scripts must be a string, mapping, or list of mappings")


def _script_from_mapping(raw: Any) -> SkillScript:
    if isinstance(raw, str):
        raw = {"path": raw}
    if not isinstance(raw, dict):
        raise SkillParseError("script entries must be mappings")
    relpath = raw.get("path") or raw.get("relpath")
    if not isinstance(relpath, str) or not relpath.strip():
        raise SkillParseError("script entry missing path")
    _validate_relpath(relpath)
    language = str(raw.get("language") or _language_from_path(relpath))
    spec_id = raw.get("spec_id")
    if spec_id is not None and not isinstance(spec_id, str):
        raise SkillParseError("script spec_id must be a string")
    timeout_seconds = int(raw.get("timeout_seconds") or 30)
    if timeout_seconds < 1 or timeout_seconds > 600:
        raise SkillParseError("script timeout_seconds must be in [1, 600]")
    return SkillScript(
        relpath=relpath,
        language=language,
        spec_id=spec_id,
        timeout_seconds=timeout_seconds,
    )


def _language_from_path(relpath: str) -> str:
    suffix = Path(relpath).suffix.lower()
    if suffix == ".py":
        return "python"
    if suffix in {".sh", ".bash"}:
        return "sh"
    if suffix in {".js", ".mjs"}:
        return "node"
    return "sh"


def _discover_resources(package_root: Path | None) -> tuple[SkillResource, ...]:
    if package_root is None or not package_root.is_dir():
        return ()
    resources: list[SkillResource] = []
    for dirname in sorted(_RESOURCE_DIRS):
        root = package_root / dirname
        if not root.is_dir():
            continue
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            relpath = path.relative_to(package_root).as_posix()
            _validate_relpath(relpath)
            try:
                path.resolve().relative_to(package_root.resolve())
            except ValueError as e:
                raise SkillParseError(
                    f"skill resource path escapes package root: {relpath!r}",
                ) from e
            import hashlib

            data = path.read_bytes()
            resources.append(
                SkillResource(
                    kind=dirname,
                    relpath=relpath,
                    size=len(data),
                    sha256=hashlib.sha256(data).hexdigest(),
                ),
            )
    return tuple(resources)


def _validate_relpath(relpath: str) -> None:
    path = Path(relpath)
    if path.is_absolute() or ".." in path.parts or not relpath.strip():
        raise SkillParseError(f"skill resource path escapes package root: {relpath!r}")
