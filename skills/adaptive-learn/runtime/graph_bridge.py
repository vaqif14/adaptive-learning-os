from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .knowledge_graph import KnowledgeGraph, load_graph, map_capabilities
from .curriculum import CurriculumContext, curriculum_authority_gate, CurriculumDecision
from .source_scope import CoverageClaim, SourceScopePolicy, analyze_source_gaps, SourceGapReport
from .source_governance import SourceRecord, SourceRights

# Bridge between an optional graphify knowledge graph and the existing
# curriculum-authority and source-scope gates. Everything here is deterministic
# and reads only an already-produced graph.json; no network, no graphify import.


@dataclass
class GraphCurriculumPlan:
    target: str
    dependency_graph_available: bool
    prerequisite_labels: list[str]
    decision: dict
    provenance: str
    community_count: int
    anchor_candidates: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "target": self.target,
            "dependency_graph_available": self.dependency_graph_available,
            "prerequisite_labels": self.prerequisite_labels,
            "curriculum_decision": self.decision,
            "graph_provenance": self.provenance,
            "community_count": self.community_count,
            "anchor_candidates": self.anchor_candidates,
            "note": "graph structure informs sequencing; it is NOT learner mastery evidence",
        }


def plan_curriculum_from_graph(graph: KnowledgeGraph, target: str, *, domain_is_volatile: bool = False) -> GraphCurriculumPlan:
    """Feed a real dependency graph into the Curriculum Authority Gate.

    The gate was written to accept ``dependency_graph_available`` but nothing in
    the shipped runtime ever set it True. A graphify graph supplies exactly that:
    a validated dependency structure the LLM adapts instead of inventing a
    sequence from nothing (SKILL.md section 5).
    """
    order_ids = graph.prerequisite_order(target)
    labels = [graph.nodes[nid].label for nid in order_ids if nid in graph.nodes]
    available = bool(order_ids)
    ctx = CurriculumContext(
        authoritative_curriculum_available=False,
        authoritative_source_ids=[],
        dependency_graph_available=available,
        domain_is_volatile=domain_is_volatile,
    )
    decision: CurriculumDecision = curriculum_authority_gate(ctx)
    anchors = [n.label for n in graph.central_nodes(5)]
    return GraphCurriculumPlan(
        target=target,
        dependency_graph_available=available,
        prerequisite_labels=labels,
        decision=decision.to_dict(),
        provenance=graph.provenance(),
        community_count=len(graph.communities()),
        anchor_candidates=anchors,
    )


def coverage_claims_from_graph(graph: KnowledgeGraph, required: list[str]) -> list[CoverageClaim]:
    """Turn graph coverage into source-scope CoverageClaims (covered/partial/missing)."""
    claims: list[CoverageClaim] = []
    for cov in map_capabilities(graph, required):
        note = None
        if cov.matched_label:
            note = f"matched:{cov.matched_label}@{cov.match_ratio}"
        claims.append(CoverageClaim(
            capability_id=cov.capability,
            status=cov.status,
            source_ids=[cov.node_id] if cov.node_id else [],
            note=note,
        ))
    return claims


def gap_report_from_graph(
    graph: KnowledgeGraph,
    required: list[str],
    policy: SourceScopePolicy | None = None,
) -> SourceGapReport:
    policy = policy or SourceScopePolicy()
    claims = coverage_claims_from_graph(graph, required)
    return analyze_source_gaps(required, claims, policy)


def graph_as_source_record(graph: KnowledgeGraph, graph_path: Path) -> SourceRecord:
    """Represent the graph itself as a governed source.

    A graph built with a semantic (LLM) pass is ``ai_derived`` and must carry its
    provenance before any reuse/retention is allowed. A pure-AST graph is a
    deterministic transform of the learner's own corpus.
    """
    is_ai = graph.has_semantic_nodes
    return SourceRecord(
        source_id=f"graphify:{graph_path.name}",
        uri=str(graph_path),
        source_type="ai_derived" if is_ai else "derived_index",
        derived_from=sorted({n.source_file for n in graph.nodes.values() if n.source_file})[:200],
        rights=SourceRights(
            access_status="local",
            rights_status="derived_from_learner_corpus",
            requires_human_review=is_ai,
        ),
        material_availability="learner_available",
    )


def load_and_plan(graph_path: Path, target: str, *, domain_is_volatile: bool = False) -> GraphCurriculumPlan:
    return plan_curriculum_from_graph(load_graph(graph_path), target, domain_is_volatile=domain_is_volatile)
