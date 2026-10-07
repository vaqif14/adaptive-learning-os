import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "adaptive-learn"
sys.path.insert(0, str(SKILL))

from runtime.cognitive_delegation import CognitiveWorkContract, DelegationContext, evaluate_delegation
from runtime.governance import ToolRequest, authorize_tool
from runtime.source_governance import SourceRecord, SourceRights, evaluate_source_use


def _ctx(**kw):
    base = dict(goal_mode="learn", ai_role="replace", requested_work=["design_api"],
                support_kind="none",
                contract=CognitiveWorkContract("design_api", protected_cognition=["design_api"], independence_required=True))
    base.update(kw)
    return DelegationContext(**base)


class DelegationGateTests(unittest.TestCase):
    def test_replace_blocked_in_learning(self):
        self.assertFalse(evaluate_delegation(_ctx()).allowed)

    def test_accessibility_cannot_launder_replace(self):
        d = evaluate_delegation(_ctx(support_kind="accessibility"))
        self.assertFalse(d.allowed)  # loophole closed
        self.assertIn("accessibility_support_does_not_justify_cognitive_replacement", d.reasons)

    def test_case_insensitive_overlap(self):
        d = evaluate_delegation(_ctx(requested_work=["DESIGN_API"], ai_role="delegate"))
        self.assertFalse(d.allowed)  # overlap detected despite different case

    def test_empty_protected_still_blocks_replace(self):
        d = evaluate_delegation(_ctx(contract=CognitiveWorkContract("x", protected_cognition=[], independence_required=True)))
        self.assertFalse(d.allowed)
        self.assertIn("protected_cognition_undeclared_fail_closed", d.reasons)

    def test_accessibility_allowed_on_nonreplace_role(self):
        d = evaluate_delegation(_ctx(ai_role="scaffold", support_kind="accessibility"))
        self.assertTrue(d.allowed)

    def test_delivery_goal_allows_replace(self):
        d = evaluate_delegation(_ctx(goal_mode="deliver_artifact"))
        self.assertTrue(d.allowed)


class ToolGateTests(unittest.TestCase):
    def test_unknown_tool_denied_without_auth(self):
        d = authorize_tool(ToolRequest(tool_id="rm_rf", operation="delete_everything"))
        self.assertFalse(d.allowed)
        self.assertIn("unknown_tool_requires_explicit_authorization", d.reasons)

    def test_registry_side_effecting_enforced(self):
        # execute_code is side_effecting in the registry; caller omits the flag.
        d = authorize_tool(ToolRequest(tool_id="execute_code", operation="run"))
        self.assertFalse(d.allowed)

    def test_authorized_side_effecting_allowed(self):
        d = authorize_tool(ToolRequest(tool_id="execute_code", operation="run", authorized=True))
        self.assertTrue(d.allowed)

    def test_known_safe_tool_allowed(self):
        self.assertTrue(authorize_tool(ToolRequest(tool_id="search", operation="query")).allowed)


class SourceRightsTests(unittest.TestCase):
    def test_prohibited_rights_hard_deny_even_with_permit(self):
        src = SourceRecord("s1", "u", "web",
                           rights=SourceRights(rights_status="prohibited", permitted_operations={"read": True}))
        d = evaluate_source_use(src, "read")
        self.assertEqual(d.status, "deny")

    def test_explicit_deny_beats_allow(self):
        src = SourceRecord("s1", "u", "web",
                           rights=SourceRights(permitted_operations={"read": False}))
        self.assertEqual(evaluate_source_use(src, "read").status, "deny")


if __name__ == "__main__":
    unittest.main()
