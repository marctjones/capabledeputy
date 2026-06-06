"""Static operator-declared resources from configs/resources.yaml.

Schema (illustrative):

  resources:
    - uri: "doc://cv.md"
      name: "Current CV"
      description: "Resume for job applications"
      mime_type: "text/markdown"
      content_path: "/home/me/docs/cv.md"  # absolute path; loaded on read
      labels: ["confidential.personal"]    # inherent labels on the content

The publisher does NO file I/O at construction time — it only stores
the metadata. Content is loaded on resources.read() so an absent file
fails the specific read call, not daemon startup.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from capabledeputy.policy.labels import LabelState, tags_for_labels_strings


class ResourceError(RuntimeError):
    """resources.yaml is malformed or a resource lookup fails."""


@dataclass(frozen=True)
class Resource:
    """One operator-declared resource."""

    uri: str
    name: str
    description: str
    mime_type: str
    content_path: Path
    tags: LabelState = field(default_factory=LabelState)

    def to_catalog_entry(self) -> dict[str, object]:
        """JSON-friendly dict suitable for resources.list output."""
        return {
            "uri": self.uri,
            "name": self.name,
            "description": self.description,
            "mime_type": self.mime_type,
            "tags": self.tags.to_dict(),
        }


@dataclass(frozen=True)
class StaticResourcePublisher:
    """Operator-declared static resource catalog."""

    resources: tuple[Resource, ...]

    def list(self, prefix: str = "") -> tuple[Resource, ...]:
        """Return resources whose uri starts with prefix (or all if empty)."""
        if not prefix:
            return self.resources
        return tuple(r for r in self.resources if r.uri.startswith(prefix))

    def get(self, uri: str) -> Resource | None:
        for r in self.resources:
            if r.uri == uri:
                return r
        return None


def load_static_resources(path: Path) -> StaticResourcePublisher:
    """Load resources from configs/resources.yaml.

    Missing file ⇒ empty publisher (operator hasn't declared any
    resources; that's a valid state, not an error).
    """
    if not path.is_file():
        return StaticResourcePublisher(resources=())
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ResourceError(f"unparseable: {path} — {e}") from e
    if data is None:
        return StaticResourcePublisher(resources=())
    raw = data.get("resources") or []
    if not isinstance(raw, list):
        raise ResourceError(f"'resources' must be a list: {path}")
    out: list[Resource] = []
    seen_uris: set[str] = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ResourceError(f"resources[{i}] is not an object")
        for required in ("uri", "name", "content_path"):
            if required not in item:
                raise ResourceError(f"resources[{i}] missing {required!r}")
        uri = str(item["uri"])
        if uri in seen_uris:
            raise ResourceError(f"resources[{i}] duplicate uri: {uri!r}")
        seen_uris.add(uri)
        content_path = Path(str(item["content_path"]))
        if not content_path.is_absolute():
            raise ResourceError(
                f"resources[{i}] content_path must be absolute: {content_path}",
            )
        labels_raw = item.get("labels") or []
        if not isinstance(labels_raw, list):
            raise ResourceError(f"resources[{i}].labels must be a list")
        tags = LabelState()  # default: no labels
        if labels_raw:
            from capabledeputy.policy.labels import _LEGACY_LABEL_STRINGS_TO_TAGS

            flat_labels = frozenset(str(label) for label in labels_raw)
            # Validate all labels are known before composing
            for label_str in flat_labels:
                if label_str not in _LEGACY_LABEL_STRINGS_TO_TAGS:
                    raise ResourceError(
                        f"resources[{i}] unknown label: {label_str!r}",
                    )
            tags = tags_for_labels_strings(flat_labels)
        out.append(
            Resource(
                uri=uri,
                name=str(item["name"]),
                description=str(item.get("description", "")),
                mime_type=str(item.get("mime_type", "text/plain")),
                content_path=content_path,
                tags=tags,
            ),
        )
    return StaticResourcePublisher(resources=tuple(out))
