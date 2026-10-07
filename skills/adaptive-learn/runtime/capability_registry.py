from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class ToolCapability:
    capability_id: str
    family: str
    side_effecting: bool = False
    can_replace_target_cognition: bool = False

    def to_dict(self):
        return asdict(self)


DEFAULT_CAPABILITIES = {
    "search": ToolCapability("search", "information"),
    "retrieve": ToolCapability("retrieve", "information"),
    "summarize": ToolCapability("summarize", "information", can_replace_target_cognition=True),
    "translate": ToolCapability("translate", "language"),
    "transcribe": ToolCapability("transcribe", "language"),
    "synthesize_speech": ToolCapability("synthesize_speech", "language"),
    "write": ToolCapability("write", "production", can_replace_target_cognition=True),
    "diagram": ToolCapability("diagram", "production"),
    "execute_code": ToolCapability("execute_code", "validation", side_effecting=True),
    "test": ToolCapability("test", "validation"),
    "critique": ToolCapability("critique", "validation"),
    "roleplay": ToolCapability("roleplay", "simulation"),
}


def get_capability(capability_id: str) -> ToolCapability:
    if capability_id not in DEFAULT_CAPABILITIES:
        raise KeyError(capability_id)
    return DEFAULT_CAPABILITIES[capability_id]
