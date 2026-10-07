from __future__ import annotations

from dataclasses import dataclass, asdict, field


OPERATIONS = {
    "read", "quote", "summarize", "transform", "locally_index", "retain",
    "redistribute", "fine_tune", "train_model"
}


@dataclass
class SourceRights:
    jurisdiction: str | None = None
    access_status: str = "unknown"
    license: str | None = None
    rights_status: str = "unknown"
    permitted_operations: dict[str, bool | None] = field(default_factory=dict)
    attribution_required: bool | None = None
    rights_reservation_detected: bool | None = None
    requires_human_review: bool = False


@dataclass
class SourceRecord:
    source_id: str
    uri: str | None
    source_type: str
    original_author: str | None = None
    publisher: str | None = None
    derived_from: list[str] = field(default_factory=list)
    rights: SourceRights = field(default_factory=SourceRights)
    material_availability: str = "unknown"  # learner_available | runtime_discovered | acquisition_required | unknown

    def to_dict(self):
        return asdict(self)


@dataclass
class SourceUseDecision:
    operation: str
    status: str
    reasons: list[str]

    def to_dict(self):
        return asdict(self)


def evaluate_source_use(source: SourceRecord, operation: str) -> SourceUseDecision:
    if operation not in OPERATIONS:
        raise ValueError(f"unsupported source operation: {operation}")
    if source.source_type == "ai_derived" and not source.derived_from:
        return SourceUseDecision(operation, "deny", ["ai_derived_source_requires_underlying_provenance"])

    # Hard deny beats everything (deny > review > allow).
    if source.rights.rights_status in {"prohibited", "denied", "all_rights_reserved_no_grant"}:
        return SourceUseDecision(operation, "deny", [f"rights_status:{source.rights.rights_status}"])

    explicit = source.rights.permitted_operations.get(operation)
    if explicit is False:
        return SourceUseDecision(operation, "deny", ["operation_explicitly_prohibited"])
    if explicit is True:
        return SourceUseDecision(operation, "allow", ["operation_explicitly_permitted"])

    # Public access is not treated as a blanket license.
    if operation in {"fine_tune", "train_model", "redistribute", "retain"}:
        return SourceUseDecision(operation, "review_required", ["rights_unknown_for_persistent_or_downstream_use"])
    if source.rights.requires_human_review:
        return SourceUseDecision(operation, "review_required", ["source_rights_marked_for_human_review"])
    return SourceUseDecision(operation, "review_required", ["public_or_accessed_does_not_imply_permission_for_operation"])
