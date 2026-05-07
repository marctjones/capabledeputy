"""Parse SKILL.md files into structured Skill records.

A skill file is YAML frontmatter (delimited by `---` lines) followed by
a prompt body. Required frontmatter fields: `name`, `description`.
Optional fields:

  - `capability_kind`: one of READ_FS / WRITE_FS / SEND_EMAIL / WEB_FETCH
    / CALENDAR_READ / CALENDAR_WRITE / QUEUE_PURCHASE. Default READ_FS.
  - `inherent_labels`: list of label strings (`confidential.health`, ...)
    that the skill's output carries.
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
from pathlib import Path
from typing import Any

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.labels import Label


class SkillParseError(ValueError):
    pass


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    capability_kind: CapabilityKind = CapabilityKind.READ_FS
    inherent_labels: frozenset[Label] = field(default_factory=frozenset)
    parameters_schema: dict[str, Any] = field(
        default_factory=lambda: {"type": "object", "properties": {}, "required": []},
    )
    target_arg: str = "target"
    schema_name: str | None = None
    body: str = ""
    source_path: Path | None = None

    def render(self, args: dict[str, Any]) -> str:
        """Substitute `{{var}}` placeholders in the body with arg values."""

        def _sub(match: re.Match[str]) -> str:
            return str(args.get(match.group(1), ""))

        return _PLACEHOLDER_RE.sub(_sub, self.body)


def parse_skill_text(text: str, *, source_path: Path | None = None) -> Skill:
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

    kind_str = front.get("capability_kind") or CapabilityKind.READ_FS.value
    try:
        capability_kind = CapabilityKind(kind_str)
    except ValueError as e:
        raise SkillParseError(f"unknown capability_kind: {kind_str}") from e

    label_strs = front.get("inherent_labels") or []
    try:
        inherent_labels = frozenset(Label(s) for s in label_strs)
    except ValueError as e:
        raise SkillParseError(f"unknown label: {e}") from e

    params = front.get("parameters") or {"type": "object", "properties": {}, "required": []}
    if not isinstance(params, dict):
        raise SkillParseError("parameters must be a JSON schema object")

    target_arg = front.get("target_arg", "target")
    if not isinstance(target_arg, str):
        raise SkillParseError("target_arg must be a string")

    schema_name = front.get("schema")
    if schema_name is not None and not isinstance(schema_name, str):
        raise SkillParseError("schema must be a string when set")

    return Skill(
        name=name,
        description=description,
        capability_kind=capability_kind,
        inherent_labels=inherent_labels,
        parameters_schema=params,
        target_arg=target_arg,
        schema_name=schema_name,
        body=body,
        source_path=source_path,
    )


def parse_skill_file(path: Path) -> Skill:
    return parse_skill_text(path.read_text(encoding="utf-8"), source_path=path)
