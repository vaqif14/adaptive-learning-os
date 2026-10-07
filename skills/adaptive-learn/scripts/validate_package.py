#!/usr/bin/env python3
from pathlib import Path
import json
import py_compile
import sys

ROOT = Path(__file__).resolve().parents[3]
SKILL = ROOT / "skills" / "adaptive-learn"

required = [
    ROOT / "plugin.json",
    ROOT / ".codex-plugin" / "plugin.json",
    ROOT / "LICENSE",
    ROOT / "README.md",
    ROOT / "DESIGN-BRIEF.md",
    ROOT / "CODEX-HANDOFF.md",
    ROOT / "FINAL-RELEASE.md",
    ROOT / "alearn",
    ROOT / "adaptive_learning_os" / "__init__.py",
    ROOT / "adaptive_learning_os" / "cli.py",
    SKILL / "SKILL.md",
    SKILL / "scripts" / "alearn.py",
    SKILL / "runtime" / "session.py",
    SKILL / "runtime" / "learning_workspace.py",
    SKILL / "templates" / "react" / "src" / "App.jsx",
    SKILL / "templates" / "react" / "package.json",
    SKILL / "runtime" / "router.py",
    SKILL / "runtime" / "policy.py",
    SKILL / "runtime" / "diagnosis.py",
    SKILL / "runtime" / "anchors.py",
    SKILL / "runtime" / "progression.py",
    SKILL / "runtime" / "projection.py",
    SKILL / "runtime" / "intents.py",
    SKILL / "runtime" / "evidence_semantics.py",
    SKILL / "runtime" / "interpretation.py",
    SKILL / "runtime" / "cognitive_delegation.py",
    SKILL / "runtime" / "challenge.py",
    SKILL / "runtime" / "performance.py",
    SKILL / "runtime" / "coverage.py",
    SKILL / "runtime" / "curriculum.py",
    SKILL / "runtime" / "source_governance.py",
    SKILL / "runtime" / "source_scope.py",
    SKILL / "runtime" / "safety.py",
    SKILL / "runtime" / "evidence_bridge.py",
    SKILL / "runtime" / "learner_model.py",
    SKILL / "runtime" / "mastery.py",
    SKILL / "runtime" / "scheduling.py",
    SKILL / "runtime" / "execution.py",
    SKILL / "runtime" / "intake.py",
    SKILL / "runtime" / "prompts.py",
    SKILL / "runtime" / "workspace_check.py",
    SKILL / "runtime" / "diagnostician.py",
    SKILL / "runtime" / "listener.py",
    SKILL / "runtime" / "server.py",
    SKILL / "runtime" / "knowledge_graph.py",
    SKILL / "runtime" / "graph_bridge.py",
    SKILL / "runtime" / "notebook_sources.py",
    SKILL / "runtime" / "notebook_cli.py",
    SKILL / "runtime" / "accessibility.py",
    SKILL / "runtime" / "simulation.py",
    SKILL / "runtime" / "governance.py",
    SKILL / "runtime" / "capability_registry.py",
    SKILL / "runtime" / "verifiers" / "python_runner.py",
    SKILL / "runtime" / "adapters" / "registry.py",
    SKILL / "adapters" / "manifests" / "generic-research.json",
    SKILL / "adapters" / "manifests" / "python-reference.json",
    SKILL / "adapters" / "manifests" / "language-learning.json",
    SKILL / "adapters" / "manifests" / "ai-ml-engineering.json",
    SKILL / "primitives" / "manifests" / "scenario-simulator.json",
    SKILL / "primitives" / "manifests" / "artifact-workspace.json",
    SKILL / "primitives" / "manifests" / "audio-dialogue.json",
    SKILL / "primitives" / "manifests" / "source-comparison.json",
    SKILL / "references" / "research-source-map.md",
    ROOT / "tests" / "test_runtime.py",
    ROOT / "tests" / "test_v522.py",
    ROOT / "tests" / "test_v530.py",
    ROOT / "tests" / "test_v550.py",
    ROOT / "tests" / "test_graph_integration.py",
    ROOT / "tests" / "test_v600_foundation.py",
    SKILL / "schemas" / "source-policy.schema.json",
    SKILL / "references" / "source-orchestration.md",
]

missing = [str(p.relative_to(ROOT)) for p in required if not p.exists()]
if missing:
    print("Missing:\n- " + "\n- ".join(missing))
    sys.exit(1)

json_dirs = [
    SKILL / "schemas",
    SKILL / "adapters" / "manifests",
    SKILL / "primitives" / "manifests",
]
for p in [ROOT / "plugin.json", ROOT / ".codex-plugin" / "plugin.json"]:
    json.loads(p.read_text(encoding="utf-8"))
for d in json_dirs:
    for p in d.glob("*.json"):
        json.loads(p.read_text(encoding="utf-8"))

# Compile every Python file so packaging errors are caught before release.
for p in list(SKILL.rglob("*.py")) + list((ROOT / "tests").rglob("*.py")):
    py_compile.compile(str(p), doraise=True)

# Release sources contain no ad-hoc browser implementation. Session workspaces
# are runtime output and intentionally include deterministic offline HTML.
for suffix in ("*.html", "*.js"):
    # decks/ = static offline presentation content (user-requested artifacts);
    # the invariant guards the RUNTIME against ad-hoc browser code, not content.
    _skip = {".learning", "decks", "build", "node_modules"}
    found = [p for p in ROOT.rglob(suffix) if not (_skip & set(p.relative_to(ROOT).parts))]
    if found:
        print(f"Unexpected M4 renderer files for {suffix}: {[str(x.relative_to(ROOT)) for x in found]}")
        sys.exit(1)

# Version consistency across packaged manifests (release hygiene).
import re as _re
_versions = {}
for _vp in [ROOT / "plugin.json", ROOT / ".codex-plugin" / "plugin.json"]:
    if _vp.exists():
        _versions[str(_vp.relative_to(ROOT))] = json.loads(_vp.read_text(encoding="utf-8")).get("version")
_pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
_m = _re.search(r'^version\s*=\s*"([^"]+)"', _pyproject, _re.M)
if _m:
    _versions["pyproject.toml"] = _m.group(1)
_distinct = set(v for v in _versions.values() if v)
if len(_distinct) > 1:
    print("Version mismatch across manifests: " + json.dumps(_versions))
    sys.exit(1)

print("Adaptive Learning OS v0.6.0 package validation: OK")
