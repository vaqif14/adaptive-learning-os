from __future__ import annotations

from dataclasses import dataclass, asdict, field


@dataclass
class ArtifactMetadata:
    artifact_id: str
    artifact_type: str
    content_hash: str | None = None
    verifier_id: str | None = None
    tests_run: int | None = None
    tests_passed: int | None = None
    lint_status: str | None = None
    source_provenance: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


@dataclass
class PerformanceEvidence:
    capability_id: str
    performance_type: str
    independence: str
    scope: str
    artifact: ArtifactMetadata | None = None
    interaction_trajectory_id: str | None = None
    rubric_id: str | None = None
    verified: bool = False

    def to_dict(self):
        return asdict(self)


def performance_kind_for_output(output_kind: str) -> str:
    mapping = {
        "code": "artifact",
        "analysis_notebook": "artifact",
        "essay": "artifact",
        "design": "artifact",
        "dialogue": "interaction_trajectory",
        "sales_call": "interaction_trajectory",
        "oral_exam": "interaction_trajectory",
        "decision": "judgment_trace",
    }
    return mapping.get(output_kind, "performance_observation")
