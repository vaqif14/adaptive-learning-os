import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "adaptive-learn"
sys.path.insert(0, str(SKILL))

from runtime.knowledge_graph import KnowledgeGraph, load_graph, map_capabilities
from runtime.graph_bridge import (
    plan_curriculum_from_graph, gap_report_from_graph,
    coverage_claims_from_graph, graph_as_source_record,
)


def _graph_dict():
    # A -> depends on B -> depends on C (via imports/calls); D isolated.
    return {
        "directed": False,
        "multigraph": False,
        "graph": {},
        "nodes": [
            {"id": "a", "label": "Replication", "norm_label": "replication", "file_type": "code", "community": 0, "_origin": "ast", "source_file": "r.py", "source_location": "L1"},
            {"id": "b", "label": "Consensus", "norm_label": "consensus", "file_type": "code", "community": 0, "_origin": "ast", "source_file": "c.py", "source_location": "L1"},
            {"id": "c", "label": "Clock", "norm_label": "clock", "file_type": "code", "community": 1, "_origin": "ast", "source_file": "k.py", "source_location": "L1"},
            {"id": "d", "label": "Isolated", "norm_label": "isolated", "file_type": "code", "community": 2, "_origin": "ast", "source_file": "i.py", "source_location": "L1"},
            {"id": "f", "label": "fileroot.py", "norm_label": ".fileroot.py", "file_type": "code", "community": 3, "_origin": "ast", "source_file": "s.py", "source_location": "L1"},
            {"id": "e", "label": "StructuralOnly", "norm_label": "structuralonly", "file_type": "code", "community": 3, "_origin": "ast", "source_file": "s.py", "source_location": "L2"},
        ],
        "links": [
            {"source": "a", "target": "b", "relation": "imports", "confidence": "EXTRACTED"},
            {"source": "b", "target": "c", "relation": "calls", "confidence": "EXTRACTED"},
            {"source": "a", "target": "c", "relation": "references", "confidence": "INFERRED"},
            {"source": "f", "target": "e", "relation": "contains", "confidence": "EXTRACTED"},
        ],
    }


def _write_graph(tmp: Path, obj=None) -> Path:
    gp = tmp / "graph.json"
    gp.write_text(json.dumps(obj or _graph_dict()), encoding="utf-8")
    return gp


class KnowledgeGraphTests(unittest.TestCase):
    def setUp(self):
        self.g = load_graph_dict()

    def test_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            g = load_graph(_write_graph(Path(td)))
        self.assertEqual(len(g.nodes), 6)
        self.assertEqual(len(g.edges), 4)

    def test_prerequisite_order_is_deps_first(self):
        order = self.g.prerequisite_order("Replication")
        labels = [self.g.nodes[n].label for n in order]
        # Clock and Consensus must come before Replication; Replication last.
        self.assertEqual(labels[-1], "Replication")
        self.assertLess(labels.index("Consensus"), labels.index("Replication"))
        self.assertLess(labels.index("Clock"), labels.index("Consensus"))

    def test_deep_chain_not_truncated(self):
        # n0 <- n1 <- ... <- n40 (each depends on the previous); must return ALL 41.
        n = 41
        obj = {
            "directed": False, "multigraph": False, "graph": {},
            "nodes": [
                {"id": f"n{i}", "label": f"Concept{i}", "norm_label": f"concept{i}",
                 "file_type": "code", "community": 0, "_origin": "ast",
                 "source_file": "d.py", "source_location": f"L{i}"}
                for i in range(n)
            ],
            "links": [
                {"source": f"n{i}", "target": f"n{i-1}", "relation": "imports", "confidence": "EXTRACTED"}
                for i in range(1, n)
            ],
        }
        with tempfile.TemporaryDirectory() as td:
            g = load_graph(_write_graph(Path(td), obj))
        order = g.prerequisite_order("Concept40")
        self.assertEqual(len(order), n)            # nothing silently dropped
        self.assertEqual(order[0], "n0")           # deepest prerequisite first
        self.assertEqual(order[-1], "n40")         # target last

    def test_class_node_reaches_method_dependencies(self):
        # A class whose deps live in its methods must still yield a prereq chain
        # (regression for the LLM-demo 'Transformer' case).
        obj = {
            "directed": False, "multigraph": False, "graph": {},
            "nodes": [
                {"id": "cls", "label": "Transformer", "norm_label": "transformer", "file_type": "code", "community": 0, "_origin": "ast", "source_file": "t.py", "source_location": "L1"},
                {"id": "fwd", "label": ".forward()", "norm_label": ".forward()", "file_type": "code", "community": 0, "_origin": "ast", "source_file": "t.py", "source_location": "L2"},
                {"id": "att", "label": "self_attention", "norm_label": "self_attention", "file_type": "code", "community": 1, "_origin": "ast", "source_file": "a.py", "source_location": "L1"},
            ],
            "links": [
                {"source": "cls", "target": "fwd", "relation": "method", "confidence": "EXTRACTED"},
                {"source": "fwd", "target": "att", "relation": "calls", "confidence": "EXTRACTED"},
            ],
        }
        with tempfile.TemporaryDirectory() as td:
            g = load_graph(_write_graph(Path(td), obj))
        order = g.prerequisite_order("Transformer")
        labels = [g.nodes[n].label for n in order]
        self.assertIn("self_attention", labels)   # reached through the method edge
        self.assertEqual(labels[-1], "Transformer")
        self.assertLess(labels.index("self_attention"), labels.index("Transformer"))

    def test_central_nodes_negative_k_is_empty(self):
        self.assertEqual(self.g.central_nodes(-1), [])
        self.assertEqual(self.g.central_nodes(0), [])

    def test_unknown_target_returns_empty(self):
        self.assertEqual(self.g.prerequisite_order("NoSuchThing"), [])

    def test_cycle_does_not_hang(self):
        obj = _graph_dict()
        obj["links"].append({"source": "c", "target": "a", "relation": "imports", "confidence": "INFERRED"})
        g = KnowledgeGraph()
        with tempfile.TemporaryDirectory() as td:
            g = load_graph(_write_graph(Path(td), obj))
        order = g.prerequisite_order("Replication")
        self.assertIn("a", order)  # terminates, includes target

    def test_coverage_covered_partial_missing(self):
        covs = {c.capability: c.status for c in map_capabilities(self.g, ["Replication", "Isolated", "StructuralOnly", "Sharding"])}
        self.assertEqual(covs["Replication"], "covered")      # dependency edges
        self.assertEqual(covs["Isolated"], "partial")          # resolves but no edges
        self.assertEqual(covs["StructuralOnly"], "partial")    # only a structural contains edge
        self.assertEqual(covs["Sharding"], "missing")          # no node

    def test_provenance_ast_vs_ai(self):
        self.assertEqual(self.g.provenance(), "deterministic_ast")
        obj = _graph_dict()
        obj["nodes"][0]["_origin"] = "semantic"
        with tempfile.TemporaryDirectory() as td:
            g = load_graph(_write_graph(Path(td), obj))
        self.assertEqual(g.provenance(), "ai_derived")

    def test_arc_direction_markers_win(self):
        obj = _graph_dict()
        # canonicalized file: _src/_tgt reversed vs source/target
        obj["links"] = [{"source": "b", "target": "a", "_src": "a", "_tgt": "b", "relation": "imports", "confidence": "EXTRACTED"}]
        with tempfile.TemporaryDirectory() as td:
            g = load_graph(_write_graph(Path(td), obj))
        self.assertTrue(g.directed_marker_used)
        self.assertEqual((g.edges[0].source, g.edges[0].target), ("a", "b"))

    def test_bad_graph_raises(self):
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "x.json"
            bad.write_text("[]", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_graph(bad)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.g = load_graph_dict()

    def test_curriculum_gate_activates_on_graph(self):
        plan = plan_curriculum_from_graph(self.g, "Replication")
        self.assertTrue(plan.dependency_graph_available)
        self.assertEqual(plan.decision["strategy"], "adapt_validated_dependency_graph")
        self.assertEqual(plan.prerequisite_labels[-1], "Replication")

    def test_curriculum_gate_falls_back_without_match(self):
        plan = plan_curriculum_from_graph(self.g, "Nonexistent")
        self.assertFalse(plan.dependency_graph_available)
        self.assertEqual(plan.decision["strategy"], "research_triangulate_then_propose")
        self.assertTrue(plan.decision["require_research"])

    def test_gap_report_from_graph(self):
        report = gap_report_from_graph(self.g, ["Replication", "Sharding"])
        self.assertIn("Replication", report.covered)
        self.assertIn("Sharding", report.missing)
        self.assertEqual(report.next_action, "discover_then_acquire_if_needed")

    def test_graph_source_record_governance(self):
        rec = graph_as_source_record(self.g, Path("graphify-out/graph.json")).to_dict()
        self.assertEqual(rec["source_type"], "derived_index")
        self.assertFalse(rec["rights"]["requires_human_review"])
        self.assertIn("r.py", rec["derived_from"])


def load_graph_dict() -> KnowledgeGraph:
    import tempfile
    td = tempfile.mkdtemp()
    return load_graph(_write_graph(Path(td)))


if __name__ == "__main__":
    unittest.main()
