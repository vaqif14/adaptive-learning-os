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


def summarize_traces(traces: list[dict]) -> dict:
    """Aggregate a session's SystemTrace records into an observability report.

    Takes the trace payloads recorded in the ledger (route decisions, tool calls,
    verifier outcomes, failures/fallbacks) and rolls them up so an operator can see
    what the system actually did in a session without replaying it by hand.
    """
    def _tally(key: str) -> dict:
        out: dict[str, int] = {}
        for t in traces:
            v = t.get(key)
            if v is None:
                continue
            out[str(v)] = out.get(str(v), 0) + 1
        return out

    tools: dict[str, int] = {}
    failures: list[str] = []
    total_model_calls = 0
    total_in = total_out = total_latency = 0
    have_in = have_out = have_latency = False
    for t in traces:
        for tool in t.get("tools_called") or []:
            tools[str(tool)] = tools.get(str(tool), 0) + 1
        total_model_calls += int(t.get("model_calls") or 0)
        if t.get("failure"):
            failures.append(str(t["failure"]))
        if t.get("input_tokens") is not None:
            total_in += int(t["input_tokens"]); have_in = True
        if t.get("output_tokens") is not None:
            total_out += int(t["output_tokens"]); have_out = True
        if t.get("latency_ms") is not None:
            total_latency += int(t["latency_ms"]); have_latency = True

    fallbacks = sum(1 for t in traces if t.get("fallback"))
    return {
        "traces": len(traces),
        "routes": _tally("route"),
        "pedagogical_intents": _tally("pedagogical_intent"),
        "policy_moves": _tally("policy_move"),
        "verifier_results": _tally("verifier_result"),
        "tools": tools,
        "total_model_calls": total_model_calls,
        "total_input_tokens": total_in if have_in else None,
        "total_output_tokens": total_out if have_out else None,
        "total_latency_ms": total_latency if have_latency else None,
        "failures": len(failures),
        "failure_detail": failures,
        "fallbacks": fallbacks,
    }
