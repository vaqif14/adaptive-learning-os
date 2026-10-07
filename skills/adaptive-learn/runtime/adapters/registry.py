from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json

from ..intents import _normalize, _tokens, _matches


@dataclass
class DomainContext:
    adapter_id: str
    topic: str
    epistemics: dict
    risk: dict
    evidence: dict
    tools: list[str]
    verifier: dict = field(default_factory=dict)
    anchor_probe: dict = field(default_factory=dict)
    sources: dict = field(default_factory=dict)
    competence_graph: dict = field(default_factory=dict)
    curriculum: dict = field(default_factory=dict)
    challenge_gate: dict = field(default_factory=dict)
    modalities: dict = field(default_factory=dict)
    authentic_environment: dict = field(default_factory=dict)
    governance: dict = field(default_factory=dict)
    practice_dimensions: list[dict] = field(default_factory=list)


class AdapterRegistry:
    def __init__(self, manifest_dirs: list[Path] | None = None):
        default = Path(__file__).resolve().parents[2] / "adapters" / "manifests"
        self.manifest_dirs = [default] + (manifest_dirs or [])
        self.manifests = self._load()

    def _load(self):
        manifests = []
        for d in self.manifest_dirs:
            if not d.exists():
                continue
            for path in sorted(d.glob("*.json")):
                obj = json.loads(path.read_text(encoding="utf-8"))
                obj["_path"] = str(path)
                manifests.append(obj)
        if not manifests:
            raise RuntimeError("no domain adapter manifests found")
        return manifests

    def match(self, topic: str) -> DomainContext:
        norm = _normalize(topic)
        tokens = _tokens(norm)
        best = None
        best_score = -10**9
        for m in self.manifests:
            match = m.get("match", {})
            score = int(match.get("priority", 0))
            kws = [str(x) for x in match.get("keywords", [])]
            # Whole-word / phrase match (not substring): "pipeline" no longer hits
            # the "pip" keyword, "Java threading" no longer hits a bare "java" inside
            # another word.
            hits = [k for k in kws if _matches(norm, tokens, k)]
            if kws and not hits:
                continue
            score += 10 * len(hits)
            if score > best_score:
                best, best_score = m, score
        if best is None:
            best = next((m for m in self.manifests if m["id"] == "generic-research"), None)
            if best is None:
                raise RuntimeError("no_matching_adapter_and_no_generic_research_fallback")
        return DomainContext(
            adapter_id=best["id"],
            topic=topic,
            epistemics=best["epistemics"],
            risk=best["risk"],
            evidence=best["evidence"],
            tools=best.get("tools", []),
            verifier=best.get("verifier", {}),
            anchor_probe=best.get("anchor_probe", {}),
            sources=best.get("sources", {}),
            competence_graph=best.get("competence_graph", {}),
            curriculum=best.get("curriculum", {}),
            challenge_gate=best.get("challenge_gate", {}),
            modalities=best.get("modalities", {}),
            authentic_environment=best.get("authentic_environment", {}),
            governance=best.get("governance", {}),
            practice_dimensions=list(best.get("practice_dimensions", [])),
        )
