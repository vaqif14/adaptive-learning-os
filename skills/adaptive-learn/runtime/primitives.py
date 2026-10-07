from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json


@dataclass(frozen=True)
class PrimitiveManifest:
    primitive_id: str
    version: str
    renderer_available: bool
    lifecycle_status: str
    supported_events: list[str]
    parameters: dict


class PrimitiveRegistry:
    def __init__(self, manifest_dirs: list[Path] | None = None):
        default = Path(__file__).resolve().parents[1] / "primitives" / "manifests"
        self.manifest_dirs = [default] + (manifest_dirs or [])
        self._items = self._load()

    def _load(self) -> dict[str, PrimitiveManifest]:
        items: dict[str, PrimitiveManifest] = {}
        for d in self.manifest_dirs:
            if not d.exists():
                continue
            for p in sorted(d.glob("*.json")):
                obj = json.loads(p.read_text(encoding="utf-8"))
                m = PrimitiveManifest(
                    primitive_id=obj["primitive_id"],
                    version=obj.get("version", "1"),
                    renderer_available=bool(obj.get("renderer_available", False)),
                    lifecycle_status=obj.get("lifecycle_status", "contract_only"),
                    supported_events=list(obj.get("supported_events", [])),
                    parameters=dict(obj.get("parameters", {})),
                )
                items[m.primitive_id] = m
        return items

    def get(self, primitive_id: str) -> PrimitiveManifest:
        if primitive_id not in self._items:
            raise KeyError(primitive_id)
        return self._items[primitive_id]

    def list(self) -> list[PrimitiveManifest]:
        return list(self._items.values())
