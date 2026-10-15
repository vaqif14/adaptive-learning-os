#!/usr/bin/env python3
from pathlib import Path
import json
import py_compile
import re
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
    SKILL / "runtime" / "course.py",
    SKILL / "runtime" / "course_state.py",
    SKILL / "runtime" / "course_content.py",
    SKILL / "runtime" / "course_sources.py",
    SKILL / "runtime" / "course_journey.py",
    SKILL / "runtime" / "course_recovery.py",
    SKILL / "scripts" / "smoke_installed.py",
    SKILL / "references" / "course-studio.md",
    SKILL / "runtime" / "rules_check.py",
    SKILL / "runtime" / "os_sandbox.py",
    SKILL / "runtime" / "rules_loopback.py",
    SKILL / "runtime" / "rules_probes" / "__init__.py",
    SKILL / "runtime" / "rules_probes" / "base.py",
    SKILL / "runtime" / "rules_probes" / "journey.py",
    *[SKILL / "runtime" / "rules_probes" / f"r{n:02d}.py" for n in range(1, 11)],
    SKILL / "runtime" / "studio.py",
    *[SKILL / "runtime" / f"studio_{m}.py" for m in (
        "agents", "handover", "jobs", "orders", "plan", "receipt", "remediate", "review",
        "staging", "store", "views", "worker")],
    SKILL / "runtime" / "learning_workspace.py",
    SKILL / "templates" / "react" / "src" / "App.jsx",
    SKILL / "templates" / "react" / "package.json",
    SKILL / "templates" / "react" / "dist" / "index.html",
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

# Course Studio packaging: every runtime subpackage must be declared in pyproject (an undeclared
# subpackage is silently missing from a wheel), and the committed UI build must be self-consistent
# (index.html references only assets that exist; no asset is orphaned from a stale build).
_pp = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
for _init in sorted((SKILL / "runtime").rglob("__init__.py")):
    _rel = _init.parent.relative_to(SKILL)
    if "node_modules" in _rel.parts or "__pycache__" in _rel.parts:
        continue
    _pkg = "alearn_skill." + ".".join(_rel.parts)
    if f'"{_pkg}"' not in _pp:
        print(f"pyproject.toml packages is missing {_pkg}: it would not ship in the wheel")
        sys.exit(1)
_dist = SKILL / "templates" / "react" / "dist"
_index = (_dist / "index.html").read_text(encoding="utf-8")
_ref = set(re.findall(r'(?:src|href)="/(assets/[^"]+)"', _index))
if not _ref:
    print("dist/index.html references no compiled assets")
    sys.exit(1)
_gone = sorted(a for a in _ref if not (_dist / a).is_file())
if _gone:
    print(f"dist/index.html references missing assets: {_gone}")
    sys.exit(1)
_chunks = sorted(p.name for p in (_dist / "assets").glob("Owner-*.js")) + sorted(p.name for p in (_dist / "assets").glob("App-*.js"))
if len(_chunks) < 2:
    print("dist/assets lacks the owner/learner chunks (Owner-*.js, App-*.js): rebuild the UI")
    sys.exit(1)

# Compile every Python file so packaging errors are caught before release.
for p in list(SKILL.rglob("*.py")) + list((ROOT / "tests").rglob("*.py")):
    py_compile.compile(str(p), doraise=True)

# Release sources contain no ad-hoc browser implementation. Session workspaces
# are runtime output and intentionally include deterministic offline HTML.
for suffix in ("*.html", "*.js"):
    # decks/ = static offline presentation content (user-requested artifacts);
    # the invariant guards the RUNTIME against ad-hoc browser code, not content.
    _skip = {".learning", "decks", "build", "node_modules"}
    compiled_ui = SKILL / "templates" / "react" / "dist"
    found = [p for p in ROOT.rglob(suffix) if not (_skip & set(p.relative_to(ROOT).parts))
             and compiled_ui not in p.parents]
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
