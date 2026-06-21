"""Standalone MCP server: curated AppleScript automation.

This server is intentionally catalog-driven. It does not expose a
generic "run arbitrary AppleScript" tool. Operators provide one or more
YAML catalogs whose entries define specific, named AppleScript tools,
their JSON input schema, argv mapping, timeout, output format, and MCP
annotations. Specialized servers for apps such as Apple Mail, Keynote,
or Microsoft Office should be thin catalogs over this substrate.

Run via:
  CAPDEP_APPLESCRIPT_CATALOG=/path/to/catalog.yaml capdep mcp-server-applescript
  CAPDEP_APPLESCRIPT_CATALOGS="/path/mail.yaml:/path/keynote.yaml" capdep mcp-server-applescript
  python -m capabledeputy.mcp_servers.applescript
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from capabledeputy.mcp_servers._common import ToolDescriptor, serve_tools

SERVER_NAME = "capdep-applescript"
DEFAULT_TIMEOUT_SECONDS = 15.0
_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_SUPPORTED_SCHEMA_TYPES = {"string", "integer", "number", "boolean", "object", "array"}


class CatalogError(ValueError):
    """AppleScript catalog is malformed."""


@dataclass(frozen=True)
class AppleScriptResult:
    stdout: str
    stderr: str
    returncode: int


class AppleScriptRunner(Protocol):
    async def __call__(
        self,
        script: str,
        argv: Sequence[str],
        timeout_seconds: float,
    ) -> AppleScriptResult: ...


@dataclass(frozen=True)
class AppleScriptToolSpec:
    name: str
    description: str
    script: str
    input_schema: dict[str, Any]
    argv: tuple[str, ...] = ()
    output_format: Literal["text", "json"] = "text"
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    app: str = ""
    bundle_id: str = ""
    read_only: bool = True
    destructive: bool = False
    annotations: dict[str, bool] = field(default_factory=dict)
    source_file: str = ""

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], *, source_file: str) -> AppleScriptToolSpec:
        name = str(raw.get("name") or "").strip()
        if not _TOOL_NAME_RE.match(name):
            raise CatalogError(
                f"{source_file}: tool name {name!r} must match {_TOOL_NAME_RE.pattern}",
            )
        script = str(raw.get("script") or "")
        if not script.strip():
            raise CatalogError(f"{source_file}: tool {name!r} is missing non-empty `script`")

        input_schema = _validate_input_schema(raw.get("input_schema"), source_file, name)
        argv = tuple(str(item) for item in (raw.get("argv") or ()))
        for arg_name in argv:
            if not arg_name or arg_name not in input_schema.get("properties", {}):
                raise CatalogError(
                    f"{source_file}: tool {name!r} argv entry {arg_name!r} "
                    "must name an input_schema property",
                )

        output_format = str(raw.get("output_format", "text")).strip().lower()
        if output_format not in {"text", "json"}:
            raise CatalogError(
                f"{source_file}: tool {name!r} output_format must be `text` or `json`",
            )

        timeout_seconds = float(raw.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        if timeout_seconds <= 0:
            raise CatalogError(f"{source_file}: tool {name!r} timeout_seconds must be positive")

        read_only = bool(raw.get("read_only", True))
        destructive = bool(raw.get("destructive", False))
        annotations_raw = raw.get("annotations") or {}
        if not isinstance(annotations_raw, Mapping):
            raise CatalogError(f"{source_file}: tool {name!r} annotations must be a mapping")
        annotations = {str(k): bool(v) for k, v in annotations_raw.items()}
        annotations.setdefault("readOnlyHint", read_only)
        annotations.setdefault("destructiveHint", destructive)
        if read_only:
            annotations.setdefault("idempotentHint", True)
        annotations.setdefault("openWorldHint", False)

        return cls(
            name=name,
            description=str(raw.get("description") or name),
            script=script,
            input_schema=input_schema,
            argv=argv,
            output_format=output_format,  # type: ignore[arg-type]
            timeout_seconds=timeout_seconds,
            app=str(raw.get("app") or ""),
            bundle_id=str(raw.get("bundle_id") or ""),
            read_only=read_only,
            destructive=destructive,
            annotations=annotations,
            source_file=source_file,
        )


def _validate_input_schema(raw: Any, source_file: str, tool_name: str) -> dict[str, Any]:
    if raw is None:
        return {"type": "object", "properties": {}, "additionalProperties": False}
    if not isinstance(raw, Mapping):
        raise CatalogError(f"{source_file}: tool {tool_name!r} input_schema must be a mapping")
    schema = dict(raw)
    if schema.get("type", "object") != "object":
        raise CatalogError(f"{source_file}: tool {tool_name!r} input_schema.type must be object")
    properties = schema.get("properties") or {}
    if not isinstance(properties, Mapping):
        raise CatalogError(f"{source_file}: tool {tool_name!r} input_schema.properties must map")
    required = schema.get("required") or []
    if not isinstance(required, list):
        raise CatalogError(
            f"{source_file}: tool {tool_name!r} input_schema.required must be a list",
        )
    for key in required:
        if str(key) not in properties:
            raise CatalogError(
                f"{source_file}: tool {tool_name!r} required field {key!r} "
                "is not declared in properties",
            )
    for prop_name, prop_schema in properties.items():
        if not isinstance(prop_schema, Mapping):
            raise CatalogError(
                f"{source_file}: tool {tool_name!r} property {prop_name!r} schema must map",
            )
        prop_type = prop_schema.get("type")
        if prop_type is not None and prop_type not in _SUPPORTED_SCHEMA_TYPES:
            raise CatalogError(
                f"{source_file}: tool {tool_name!r} property {prop_name!r} "
                f"uses unsupported type {prop_type!r}",
            )
    schema.setdefault("type", "object")
    schema.setdefault("properties", dict(properties))
    schema.setdefault("additionalProperties", False)
    return schema


def catalog_paths_from_env(environ: Mapping[str, str] | None = None) -> list[Path]:
    env = environ if environ is not None else os.environ
    raw = env.get("CAPDEP_APPLESCRIPT_CATALOGS") or env.get("CAPDEP_APPLESCRIPT_CATALOG") or ""
    return [Path(item).expanduser() for item in raw.split(os.pathsep) if item.strip()]


def load_catalogs(paths: Sequence[Path]) -> list[AppleScriptToolSpec]:
    specs: list[AppleScriptToolSpec] = []
    seen: set[str] = set()
    for path in _expand_catalog_paths(paths):
        for spec in load_catalog(path):
            if spec.name in seen:
                raise CatalogError(f"{path}: duplicate AppleScript tool name {spec.name!r}")
            seen.add(spec.name)
            specs.append(spec)
    return specs


def _expand_catalog_paths(paths: Sequence[Path]) -> list[Path]:
    expanded: list[Path] = []
    for path in paths:
        if path.is_dir():
            expanded.extend(sorted(p for p in path.glob("*.yaml") if p.is_file()))
            expanded.extend(sorted(p for p in path.glob("*.yml") if p.is_file()))
            continue
        if not path.is_file():
            raise CatalogError(f"AppleScript catalog path does not exist or is not a file: {path}")
        expanded.append(path)
    return expanded


def load_catalog(path: Path) -> list[AppleScriptToolSpec]:
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as e:  # pragma: no cover - PyYAML is a project dependency.
        raise RuntimeError("PyYAML is required for AppleScript catalogs") from e

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, Mapping):
        raise CatalogError(f"{path}: top-level AppleScript catalog must be a mapping")
    schema_version = int(raw.get("schema_version", 1))
    if schema_version != 1:
        raise CatalogError(f"{path}: unsupported schema_version {schema_version}")
    tools_raw = raw.get("tools") or []
    if not isinstance(tools_raw, list):
        raise CatalogError(f"{path}: `tools` must be a list")
    return [
        AppleScriptToolSpec.from_dict(tool_raw, source_file=str(path))
        for tool_raw in tools_raw
        if _require_mapping(tool_raw, path)
    ]


def _require_mapping(raw: Any, path: Path) -> bool:
    if not isinstance(raw, Mapping):
        raise CatalogError(f"{path}: every tools entry must be a mapping")
    return True


def _validate_call_args(spec: AppleScriptToolSpec, args: Mapping[str, Any]) -> None:
    schema = spec.input_schema
    properties: Mapping[str, Mapping[str, Any]] = schema.get("properties") or {}
    required = {str(key) for key in (schema.get("required") or [])}
    missing = sorted(key for key in required if key not in args)
    if missing:
        raise ValueError(f"{spec.name}: missing required argument(s): {', '.join(missing)}")
    if schema.get("additionalProperties") is False:
        unknown = sorted(set(args) - set(properties))
        if unknown:
            raise ValueError(f"{spec.name}: unknown argument(s): {', '.join(unknown)}")
    for key, value in args.items():
        prop_schema = properties.get(key)
        if prop_schema is None:
            continue
        _validate_arg_type(spec.name, key, value, str(prop_schema.get("type", "")))


def _validate_arg_type(tool_name: str, key: str, value: Any, schema_type: str) -> None:
    if not schema_type:
        return
    valid = (
        (schema_type == "string" and isinstance(value, str))
        or (schema_type == "integer" and isinstance(value, int) and not isinstance(value, bool))
        or (
            schema_type == "number"
            and isinstance(value, int | float)
            and not isinstance(value, bool)
        )
        or (schema_type == "boolean" and isinstance(value, bool))
        or (schema_type == "object" and isinstance(value, Mapping))
        or (schema_type == "array" and isinstance(value, list))
    )
    if not valid:
        raise ValueError(f"{tool_name}: argument {key!r} must be {schema_type}")


def _argv_for_spec(spec: AppleScriptToolSpec, args: Mapping[str, Any]) -> list[str]:
    properties: Mapping[str, Mapping[str, Any]] = spec.input_schema.get("properties") or {}
    argv: list[str] = []
    for name in spec.argv:
        if name in args:
            value = args[name]
        elif "default" in properties[name]:
            value = properties[name]["default"]
        else:
            value = ""
        argv.append(_argv_value(value))
    return argv


def _argv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float | str):
        return str(value)
    return json.dumps(value, sort_keys=True)


def _make_handler(spec: AppleScriptToolSpec, runner: AppleScriptRunner):
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        _validate_call_args(spec, args)
        result = await runner(spec.script, _argv_for_spec(spec, args), spec.timeout_seconds)
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        if result.returncode != 0:
            detail = stderr or stdout or "no stderr"
            raise RuntimeError(f"{spec.name}: AppleScript failed ({result.returncode}): {detail}")
        payload: dict[str, Any] = {
            "tool": spec.name,
            "app": spec.app,
            "bundle_id": spec.bundle_id,
            "output_format": spec.output_format,
        }
        if stderr:
            payload["stderr"] = stderr
        if spec.output_format == "json":
            try:
                payload["result"] = json.loads(stdout or "{}")
            except json.JSONDecodeError as e:
                raise RuntimeError(f"{spec.name}: AppleScript returned invalid JSON") from e
        else:
            payload["output"] = stdout
        return payload

    return _handler


def specs_from_dicts(
    raw_specs: Sequence[Mapping[str, Any]],
    *,
    source_file: str,
) -> list[AppleScriptToolSpec]:
    """Build validated AppleScript specs from trusted in-repo definitions."""
    return [AppleScriptToolSpec.from_dict(raw, source_file=source_file) for raw in raw_specs]


def descriptors_from_specs(
    specs: Sequence[AppleScriptToolSpec],
    runner: AppleScriptRunner,
) -> list[ToolDescriptor]:
    """Convert validated AppleScript specs to MCP tool descriptors."""
    return [
        ToolDescriptor(
            name=spec.name,
            description=spec.description,
            input_schema=spec.input_schema,
            handler=_make_handler(spec, runner),
            annotations=spec.annotations,
        )
        for spec in specs
    ]


async def run_osascript(
    script: str,
    argv: Sequence[str],
    timeout_seconds: float,
) -> AppleScriptResult:
    if sys.platform != "darwin" and os.environ.get("CAPDEP_APPLESCRIPT_ALLOW_NON_DARWIN") != "1":
        raise RuntimeError("AppleScript MCP server requires macOS (`osascript`)")
    proc = await asyncio.create_subprocess_exec(
        "/usr/bin/osascript",
        "-l",
        "AppleScript",
        "-e",
        script,
        "--",
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise TimeoutError(f"AppleScript timed out after {timeout_seconds:g}s") from None
    return AppleScriptResult(
        stdout=stdout_b.decode("utf-8", errors="replace"),
        stderr=stderr_b.decode("utf-8", errors="replace"),
        returncode=int(proc.returncode or 0),
    )


def tools(
    *,
    catalog_paths: Sequence[Path] | None = None,
    runner: AppleScriptRunner = run_osascript,
) -> list[ToolDescriptor]:
    paths = list(catalog_paths) if catalog_paths is not None else catalog_paths_from_env()
    specs = load_catalogs(paths) if paths else []
    return descriptors_from_specs(specs, runner)


async def serve(*, catalog_paths: Sequence[Path] | None = None) -> None:
    await serve_tools(SERVER_NAME, tools(catalog_paths=catalog_paths))


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
