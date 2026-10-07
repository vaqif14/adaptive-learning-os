from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass
class CurriculumContext:
    authoritative_curriculum_available: bool = False
    authoritative_source_ids: list[str] | None = None
    dependency_graph_available: bool = False
    domain_is_volatile: bool = False


@dataclass
class CurriculumDecision:
    strategy: str
    reasons: list[str]
    require_research: bool

    def to_dict(self):
        return asdict(self)


def curriculum_authority_gate(ctx: CurriculumContext) -> CurriculumDecision:
    sources = ctx.authoritative_source_ids or []
    if ctx.authoritative_curriculum_available and sources:
        return CurriculumDecision(
            "adapt_authoritative_sequence",
            ["llm_is_curriculum_adapter_not_unconstrained_curriculum_author"],
            require_research=ctx.domain_is_volatile,
        )
    if ctx.dependency_graph_available:
        return CurriculumDecision(
            "adapt_validated_dependency_graph",
            ["use_existing_domain_structure_before_llm_generation"],
            require_research=ctx.domain_is_volatile,
        )
    return CurriculumDecision(
        "research_triangulate_then_propose",
        ["no_authoritative_sequence_available", "preserve_uncertainty_about_prerequisites"],
        require_research=True,
    )
