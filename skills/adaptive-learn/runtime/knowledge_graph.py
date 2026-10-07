from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
import json

# Graphify (https://github.com/Graphify-Labs/graphify) is an OPTIONAL, external
# source provider. It turns a corpus (code, docs, PDFs) into a portable
# ``graph.json`` knowledge graph with deterministic AST edges, confidence labels
# (EXTRACTED/INFERRED/AMBIGUOUS) and community detection.
#
# This module is the loose-coupling boundary the product requires: the runtime
# NEVER imports the ``graphify`` package and keeps zero third-party dependencies.
# It only consumes the portable ``graph.json`` artifact. If graphify is absent,
# the learner (or host agent) produces the file some other way or the graph-backed
# curriculum path is simply skipped. This mirrors the source-governance rule that
# providers must be swappable and never hard-wired into the core.

# Relations that express "A depends on / builds on B" (B is a prerequisite of A).
DEPENDENCY_RELATIONS = frozenset({
    "imports", "imports_from", "calls", "inherits", "uses",
    "references", "depends_on", "implements", "re_exports",
})

# Relations that express "A is a structural part of B" (not a prerequisite edge).
STRUCTURAL_RELATIONS = frozenset({"contains", "method"})
# For curriculum ordering we also descend through class->method edges so a
# class's real dependencies (reachable only via its methods) are not cut off.
CURRICULUM_TRAVERSAL = DEPENDENCY_RELATIONS | {"method"}

CONFIDENCE_RANK = {"EXTRACTED": 2, "INFERRED": 1, "AMBIGUOUS": 0}


@dataclass
class GraphEdge:
    source: str
    target: str
    relation: str
    confidence: str = "INFERRED"

    @property
    def is_dependency(self) -> bool:
        return self.relation in DEPENDENCY_RELATIONS


@dataclass
class GraphNode:
    node_id: str
    label: str
    norm_label: str = ""
    file_type: str = "code"
    community: int | None = None
    origin: str = "unknown"
    source_file: str | None = None
    source_location: str | None = None


@dataclass
class KnowledgeGraph:
    nodes: dict[str, GraphNode] = field(default_factory=dict)
    edges: list[GraphEdge] = field(default_factory=list)
    directed_marker_used: bool = False

    # --- provenance -------------------------------------------------------
    @property
    def has_semantic_nodes(self) -> bool:
        """True if any node came from a (non-deterministic) LLM semantic pass.

        Such a graph is partly ``ai_derived`` and MUST keep its provenance when
        it feeds source governance (see ``source_governance.SourceRecord``).
        """
        return any(n.origin not in {"ast", "unknown", ""} for n in self.nodes.values())

    def provenance(self) -> str:
        return "ai_derived" if self.has_semantic_nodes else "deterministic_ast"

    # --- lookups ----------------------------------------------------------
    def resolve(self, name: str, *, min_ratio: float = 0.82) -> GraphNode | None:
        """Resolve a capability/topic name to a node.

        Exact id, exact label, exact normalized label, then a conservative fuzzy
        match. Returns the best node or ``None`` — never guesses silently below
        ``min_ratio`` so coverage stays honest.
        """
        if name in self.nodes:
            return self.nodes[name]
        key = _normalize(name)
        if not key:
            return None
        exact = [n for n in self.nodes.values() if n.norm_label == key or _normalize(n.label) == key]
        if exact:
            return exact[0]
        best: GraphNode | None = None
        best_ratio = min_ratio
        for n in self.nodes.values():
            ratio = SequenceMatcher(None, key, n.norm_label or _normalize(n.label)).ratio()
            if ratio > best_ratio:
                best, best_ratio = n, ratio
        return best

    def dependency_edges(self) -> list[GraphEdge]:
        return [e for e in self.edges if e.is_dependency]

    def prerequisites_of(self, node_id: str) -> list[str]:
        """Direct prerequisites: nodes ``node_id`` depends on (one hop).

        Follows dependency edges AND class->method edges (so a class reaches the
        dependencies that live inside its methods), but never file-container nodes.
        """
        out = []
        for e in self.edges:
            if e.source != node_id or e.target not in self.nodes:
                continue
            if e.relation in CURRICULUM_TRAVERSAL:
                if not _looks_like_file(self.nodes[e.target].label):
                    out.append(e.target)
        return out

    def prerequisite_order(self, target: str) -> list[str]:
        """Deterministic prerequisite-first ordering for learning ``target``.

        Walks the dependency subgraph the target transitively relies on and
        returns node ids in depth-first post-order (deepest prerequisite first,
        the target last). Implemented iteratively so arbitrarily deep chains are
        returned in full (no recursion limit, no silent depth truncation).
        Cycles are broken deterministically; a target not in the graph yields an
        empty list (caller then falls back to research).
        """
        node = self.resolve(target)
        if node is None:
            return []
        order: list[str] = []
        seen: set[str] = set()
        on_path: set[str] = set()
        # Each frame is (node_id, children_expanded?).
        stack: list[tuple[str, bool]] = [(node.node_id, False)]
        while stack:
            nid, expanded = stack.pop()
            if expanded:
                on_path.discard(nid)
                if nid not in seen:
                    seen.add(nid)
                    order.append(nid)
                continue
            if nid in seen or nid in on_path:
                continue  # already emitted, or a cycle back-edge
            on_path.add(nid)
            stack.append((nid, True))
            # Push deps reversed so they pop in sorted order (stable output).
            for dep in sorted(self.prerequisites_of(nid), reverse=True):
                if dep not in seen:
                    stack.append((dep, False))
        return order

    def central_nodes(self, k: int = 5) -> list[GraphNode]:
        """Highest-degree concept nodes — seeds for a cold-start anchor probe.

        Excludes file-container nodes (labels that look like a filename) so probes
        anchor on concepts, not files.
        """
        degree: dict[str, int] = {nid: 0 for nid in self.nodes}
        for e in self.edges:
            if e.source in degree:
                degree[e.source] += 1
            if e.target in degree:
                degree[e.target] += 1
        ranked = sorted(
            (n for n in self.nodes.values() if not _looks_like_file(n.label)),
            key=lambda n: (-degree.get(n.node_id, 0), n.label),
        )
        return ranked[: max(0, k)]

    def communities(self) -> dict[int, list[str]]:
        out: dict[int, list[str]] = {}
        for n in self.nodes.values():
            if n.community is None:
                continue
            out.setdefault(n.community, []).append(n.label)
        for labels in out.values():
            labels.sort()
        return dict(sorted(out.items()))


@dataclass
class CapabilityCoverage:
    capability: str
    status: str  # covered | partial | missing
    node_id: str | None = None
    matched_label: str | None = None
    match_ratio: float = 0.0
    evidence_edges: int = 0


def _normalize(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _looks_like_file(label: str) -> bool:
    return "." in label and label.rsplit(".", 1)[-1].isalpha() and " " not in label


def load_graph(path: Path) -> KnowledgeGraph:
    """Load a graphify ``graph.json`` with edge direction preserved.

    graph.json is written ``"directed": false`` but every link is directed by arc
    order (``source`` -> ``target``); canonicalized files carry ``_src``/``_tgt``
    markers that win where present (graphify ARCHITECTURE.md). A naive
    ``node_link_graph`` load loses this, so we read the raw dict and honor the
    markers ourselves rather than depending on graphify/networkx.
    """
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid_json:{e}") from e
    if not isinstance(raw, dict) or "nodes" not in raw:
        raise ValueError("not_a_graphify_graph_json")
    g = KnowledgeGraph()
    for n in raw.get("nodes", []):
        nid = n.get("id")
        if not nid:
            continue
        g.nodes[nid] = GraphNode(
            node_id=nid,
            label=n.get("label", nid),
            norm_label=n.get("norm_label") or _normalize(n.get("label", nid)),
            file_type=n.get("file_type", "code"),
            community=n.get("community"),
            origin=n.get("_origin", "unknown"),
            source_file=n.get("source_file"),
            source_location=n.get("source_location"),
        )
    links = raw.get("links")
    if links is None:
        links = raw.get("edges", [])
    for e in links:
        src = e.get("_src", e.get("source"))
        tgt = e.get("_tgt", e.get("target"))
        if "_src" in e or "_tgt" in e:
            g.directed_marker_used = True
        if src is None or tgt is None:
            continue
        g.edges.append(GraphEdge(
            source=src,
            target=tgt,
            relation=e.get("relation", "references"),
            confidence=e.get("confidence", "INFERRED"),
        ))
    return g


def map_capabilities(graph: KnowledgeGraph, required: list[str]) -> list[CapabilityCoverage]:
    """Map required capabilities onto graph nodes -> covered/partial/missing.

    - covered: a node resolves AND has >=1 edge (connected, i.e. the graph
      actually relates it to other material);
    - partial: a node resolves but is isolated (present but unexplained);
    - missing: no node resolves above the fuzzy threshold.

    This is corpus COVERAGE, never learner mastery (see SKILL.md section 12).
    """
    out: list[CapabilityCoverage] = []
    for cap in required:
        node = graph.resolve(cap)
        if node is None:
            out.append(CapabilityCoverage(cap, "missing"))
            continue
        # A node connected only by structural edges (its file "contains" it) is
        # present but not explained by relationships to other concepts -> partial.
        deg = sum(
            1 for e in graph.edges
            if (e.source == node.node_id or e.target == node.node_id)
            and e.relation not in STRUCTURAL_RELATIONS
        )
        ratio = SequenceMatcher(None, _normalize(cap), node.norm_label or _normalize(node.label)).ratio()
        status = "covered" if deg > 0 else "partial"
        out.append(CapabilityCoverage(
            cap, status, node_id=node.node_id, matched_label=node.label,
            match_ratio=round(ratio, 3), evidence_edges=deg,
        ))
    return out
