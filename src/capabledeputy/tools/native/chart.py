"""Generate chart images for inline GUI display via ``chart.plot``."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.effect_class import EffectClass, Operation
from capabledeputy.tools.registry import ToolContext, ToolDefinition, ToolResult

_CHART_DIR = Path.home() / ".capdep" / "work" / "charts"
_POLICY_TARGET = str(_CHART_DIR / "plot.png")


def _chart_output_dir() -> Path:
    path = _CHART_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def _render_chart(
    *,
    chart_type: str,
    title: str,
    x_labels: list[str],
    y_values: list[float],
    ylabel: str,
    output_path: Path,
) -> None:
    try:
        import matplotlib  # pyright: ignore[reportMissingImports]

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # pyright: ignore[reportMissingImports]
    except ImportError as exc:
        raise RuntimeError(
            "matplotlib is required for chart.plot — install with "
            "`uv sync --all-groups --extra charts`",
        ) from exc

    if len(x_labels) != len(y_values):
        raise ValueError("x and y must have the same length")
    if not x_labels:
        raise ValueError("x and y must be non-empty")

    fig, ax = plt.subplots(figsize=(9, 5), dpi=120)
    kind = chart_type.strip().lower()
    if kind == "bar":
        ax.bar(x_labels, y_values, color="#4C78A8")
    else:
        ax.plot(x_labels, y_values, marker="o", linewidth=2, color="#4C78A8")
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.25)
    fig.autofmt_xdate()
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, format="png", bbox_inches="tight")
    plt.close(fig)


def make_chart_tools() -> list[ToolDefinition]:
    async def chart_plot(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        chart_type = str(args.get("chart_type") or "line").strip().lower()
        if chart_type not in {"line", "bar"}:
            return ToolResult(output={"ok": False, "error": "chart_type must be 'line' or 'bar'"})

        title = str(args.get("title") or "Chart").strip() or "Chart"
        ylabel = str(args.get("ylabel") or "Value").strip() or "Value"
        x_raw = args.get("x") or []
        y_raw = args.get("y") or []
        if not isinstance(x_raw, list) or not isinstance(y_raw, list):
            return ToolResult(output={"ok": False, "error": "x and y must be arrays"})
        x_labels = [str(item) for item in x_raw]
        try:
            y_values = [float(item) for item in y_raw]
        except (TypeError, ValueError):
            return ToolResult(output={"ok": False, "error": "y values must be numbers"})

        alt = str(args.get("alt") or title).strip() or title
        filename = str(args.get("filename") or f"{uuid4().hex}.png").strip()
        if not filename.lower().endswith(".png"):
            filename += ".png"
        output_path = _chart_output_dir() / Path(filename).name

        try:
            _render_chart(
                chart_type=chart_type,
                title=title,
                x_labels=x_labels,
                y_values=y_values,
                ylabel=ylabel,
                output_path=output_path,
            )
        except (RuntimeError, ValueError) as exc:
            return ToolResult(output={"ok": False, "error": str(exc)})

        path_str = str(output_path)
        markdown = f"![{alt}]({path_str})"
        return ToolResult(
            output={
                "ok": True,
                "chart_type": chart_type,
                "title": title,
                "plot": path_str,
                "image_path": path_str,
                "alt": alt,
                "markdown": markdown,
                "content": f"Generated {chart_type} chart: {title}\n\n{markdown}\n",
            },
        )

    return [
        ToolDefinition(
            name="chart.plot",
            effect_class="data.create_local",
            operations=(Operation(EffectClass.MUTATE_LOCAL, subtype="chart.plot"),),
            risk_ids=("RISK-DESTRUCTIVE-WRITE",),
            surfaces_destination_id=True,
            default_reversibility={"degree": "reversible", "agent": "system"},
            tool_provenance="operator-curated",
            description=(
                "Generate a line or bar chart PNG and return markdown for inline "
                "display in CapDepMac. Writes to ~/.capdep/work/charts/.\n\n"
                "USE THIS WHEN: the user asks for a chart, graph, plot, or visual "
                "of numeric data. Gather data first (web search, memory, or user "
                "numbers) when needed, then call this tool with matching x labels "
                "and y values. Include the returned `markdown` field in your final "
                "reply so the GUI renders the image inline."
            ),
            capability_kind=CapabilityKind.CREATE_FS,
            handler=chart_plot,
            target_template=_POLICY_TARGET,
            parameters_schema={
                "type": "object",
                "properties": {
                    "chart_type": {
                        "type": "string",
                        "enum": ["line", "bar"],
                        "description": "Chart style (default line).",
                    },
                    "title": {"type": "string", "description": "Chart title."},
                    "ylabel": {
                        "type": "string",
                        "description": "Y-axis label (default Value).",
                    },
                    "x": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "X-axis category labels.",
                    },
                    "y": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "Numeric values aligned with x.",
                    },
                    "alt": {
                        "type": "string",
                        "description": "Alt text for inline markdown image.",
                    },
                    "filename": {
                        "type": "string",
                        "description": "Optional output filename (png).",
                    },
                },
                "required": ["x", "y"],
            },
        ),
    ]
