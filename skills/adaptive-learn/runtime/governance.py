from __future__ import annotations

from dataclasses import dataclass, asdict, field

from .capability_registry import DEFAULT_CAPABILITIES


@dataclass
class ToolRequest:
    tool_id: str
    operation: str
    high_risk: bool = False
    side_effecting: bool = False
    authorized: bool = False
    resource_cost_class: str = "standard"


@dataclass
class ToolPolicyDecision:
    allowed: bool
    status: str
    reasons: list[str]

    def to_dict(self):
        return asdict(self)


def authorize_tool(req: ToolRequest) -> ToolPolicyDecision:
    """Deny > allow. Side-effect/high-risk facts are taken from the capability
    registry OR the caller (whichever says 'dangerous'), never softened by the
    caller. Unknown tools are denied unless explicitly authorized."""
    cap = DEFAULT_CAPABILITIES.get(req.tool_id)
    registry_side_effecting = bool(cap.side_effecting) if cap else False
    effective_side_effecting = req.side_effecting or registry_side_effecting

    if cap is None and not req.authorized:
        return ToolPolicyDecision(False, "deny", ["unknown_tool_requires_explicit_authorization"])
    if req.high_risk and not req.authorized:
        return ToolPolicyDecision(False, "deny", ["high_risk_tool_requires_explicit_authorization"])
    if effective_side_effecting and not req.authorized:
        reason = "side_effecting_tool_requires_authorization"
        if registry_side_effecting and not req.side_effecting:
            reason = "registry_marks_tool_side_effecting_requires_authorization"
        return ToolPolicyDecision(False, "deny", [reason])
    return ToolPolicyDecision(True, "allow", ["tool_policy_gate_passed"])


@dataclass
class SystemTrace:
    trace_id: str
    route: str
    pedagogical_intent: str | None = None
    model_calls: int = 0
    tools_called: list[str] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None
    verifier_result: str | None = None
    policy_move: str | None = None
    failure: str | None = None
    fallback: str | None = None

    def to_dict(self):
        return asdict(self)
