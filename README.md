# Adaptive Learning OS — Release v0.6.0

A **thin-kernel, evidence-first, domain-sensitive learning runtime** for Codex/agent workflows.

This release integrates the full design line developed through v5.x without turning the kernel into a monolithic tutor. The kernel remains small; richer behavior lives in contracts, adapters, gates, verifiers, registries, and auditable evidence.

## Product thesis

> **Maximize useful cognition performed by the learner, not the amount of help produced by the AI.**

The system does not attempt to model the learner's brain. It stores only the minimum evidence needed to improve the next decision.

## Core architecture

```text
USER
  ↓
STUDY / WORK INTENT ROUTER
  ↓
GOAL MODE
  learn | perform_with_assistance | deliver_artifact | quick_reference
  ↓
COGNITIVE WORK CONTRACT
  protected cognition | delegable work | independence requirement
  ↓
SESSION KERNEL
  ↓
DOMAIN ADAPTER + CURRICULUM AUTHORITY + SOURCE GOVERNANCE
  ↓
APPEND-ONLY EVIDENCE LEDGER  ← source of truth
  ↓
REBUILDABLE LEARNER EVIDENCE PROJECTION
  ↓
POLICY ENGINE
  ├─ direct answer
  ├─ teach/practice/review
  ├─ challenge gate / prove-it-first
  ├─ optional diagnosis (rare)
  └─ simulation / performance evidence
  ↓
DETERMINISTIC VERIFIER / RUBRIC / AUTHENTIC ENVIRONMENT
```

Cross-cutting controls:

```text
AI-use policy
Cognitive delegation gate
Accessibility accommodations
Practice coverage
Simulation fidelity
Tool governance
System observability
Source rights/provenance
```

## What is implemented now

### M0–M3 runtime

- Interaction and Study Intent routing.
- Quick-reference vs learning vs delivery semantics.
- Append-only event/evidence ledger.
- Rebuildable learner evidence projection.
- Goal/resource/deadline-aware Learning Contract.
- Two-class source orchestration: available materials + runtime-resolved missing materials.
- Default adaptive source policy: no file upload required to begin; discover authoritative missing coverage automatically; recommend acquisition only as a fallback.
- Cold-start high-information Anchor Probe.
- Challenge Gate / prove-it-first contract.
- Three-level diagnosis restraint.
- Adaptive evidence progression with expert skip.
- Support Provenance rather than “assistance debt”.
- Deterministic Python verifier for low-risk snippets.
- Evidence-format ceilings and compression ≠ mastery.
- Question-interpretation gate before misconception inference.
- Review-item selection by capability type.
- Cognitive Delegation Gate and Protected Cognition contract.
- Curriculum Authority Gate.
- Practice Coverage planner.
- Artifact/performance evidence contracts.
- Source Rights & Provenance gate.
- Accessibility support semantics.
- Simulation Fidelity evidence caps.
- Tool Policy Gate and system trace model.
- Language-learning reference adapter showing multimodal/authentic-environment requirements.

### Offline learning workspace + M4 contracts

The package contains declarative primitive manifests for:

- `scenario_simulator`
- `artifact_workspace`
- `audio_dialogue`
- `source_comparison`

These M4 primitives remain **contracts only**. Learning starts now generate a separate offline HTML roadmap and task workspace automatically.

## CLI

No Enterprise service and no mandatory source upload are required. After extracting the ZIP:

```bash
chmod +x ./alearn
./alearn --help
```

You can also run the Python entry directly:

```bash
python3 skills/adaptive-learn/scripts/alearn.py --help
```

`pip install -e .` is optional for environments that already have standard Python build tooling; the root launcher is the dependency-free path.

## Study Intent Catalog

The router can distinguish:

```text
quick_reference
compress_source
build_plan
learn_concept
decompose_task
build_with_help
critique_artifact
interpret_question
simulate_performance
practice_retrieval
deliver_artifact
```

These are interaction semantics, not subagents.

## Learning vs delivery

A central invariant is:

```text
Task completion ≠ learning.
AI-assisted performance ≠ independent capability.
```

If the user needs a deliverable, the AI may do more work. If the user wants to learn a capability, the Cognitive Delegation Gate protects the cognition the learner must perform themselves.

Example:

```yaml
target_capability: design_scalable_backend
protected_cognition:
  - identify_constraints
  - generate_options
  - compare_tradeoffs
  - justify_decision
delegable_work:
  - formatting
  - diagram_rendering
  - citation_formatting
```

## Challenge Gate / Prove-It-First

Experienced learners can skip redundant instruction by demonstrating capability in an authentic task.

A challenge pass is **capability-specific evidence**, never a global learner-level flag.

## Performance evidence

Answers are not the only evidence form.

Examples:

- programming → executable code + tests;
- data → analysis artifact + reproducible result;
- history → source-grounded argument;
- language → dialogue/writing/listening performance;
- sales → interaction trajectory;
- professional judgment → decision trace and justification.

## Source Orchestration + Governance

The learner does **not** have to provide materials every time. The runtime exposes two material classes:

```text
AVAILABLE
learner files/URLs + canonical sources + lawfully discovered sources usable now

MISSING
required capability coverage that current sources do not support well enough
```

Default policy is `adaptive_hybrid`:

```text
use available materials
→ detect missing/partial coverage
→ automatically discover authoritative open/current sources
→ run provenance/freshness/rights governance
→ use lawful sufficient sources
→ if a critical gap remains, recommend the best book/paper/resource for the learner to obtain
```

The runtime asks the learner for a source only when a critical unresolved gap really requires private/paid/inaccessible material. `strict_closed_world` remains available when the learner explicitly wants to stay inside a bounded corpus.

NotebookLM is integrated through the installed `notebooklm` CLI (or `nlm`). Learning starts enumerate available notebooks for session-specific selection; no Enterprise connector is required. An explicit `--without-notebooklm` option allows offline starts.

The runtime also separates:

```text
Epistemic Gate: Can I trust this source?
Rights Gate: May I use this source this way?
```

Frozen invariants:

- Public access is not blanket permission.
- Read/summarize/index/retain/redistribute/train are separate operations.
- AI-derived content keeps provenance to underlying sources.
- Unknown rights remain unknown; the model may not invent a license.
- User-supplied confidential material never becomes global training data automatically.
- Corpus coverage is not learner mastery evidence.

## Accessibility

Accessibility support is not pedagogical assistance.

```text
text-to-speech → access support
small hint      → pedagogical support
AI writes logic → cognitive delegation
```

Access accommodations do not reduce independence evidence merely because an accommodation was used.

## Simulation Fidelity

AI simulation is not automatically real-world transfer.

Example: an AI language conversation can support near-transfer evidence but does not prove real-world robustness to human spontaneity, accent variation, or social pressure.

## Curriculum Authority

The LLM is a **curriculum adapter, not an unconstrained curriculum authority**.

Priority:

1. authoritative curriculum/spec/dependency graph if available;
2. adapt it to the learner goal;
3. otherwise research and triangulate before proposing sequencing.

## Practice Coverage

The system tracks coverage dimensions to prevent repetitive AI practice loops. It varies surface form/context while preserving the target underlying capability.

## Deterministic Python verifier

The included verifier uses AST policy checks, isolated Python mode, temporary working directory, timeout, resource limits, and optional expected output/trusted assertions.

It is a **best-effort local verifier**, not a hardened security sandbox. Arbitrary untrusted code requires container/VM isolation.

## Source workflow

Start with or without material:

```bash
# No source upload required; missing coverage will be planned for discovery.
alearn start --topic "Distributed Systems" --goal "Learn storage internals"

# Optional: register material you already have.
alearn sources-register --session <SESSION_ID> --resource ./book.pdf --canonical-resource https://raft.github.io/raft.pdf

# Show coverage/discovery/acquisition plan.
alearn sources-plan --session <SESSION_ID> \
  --required-capability replication \
  --required-capability consensus \
  --covered-capability replication
```

The host agent should execute the discovery request automatically with its research/search tools. Only unresolved critical closed-source gaps are escalated to the learner.

## Validation

```bash
python skills/adaptive-learn/scripts/validate_package.py
python -m unittest discover -s tests -v
```

The final release is also verified from a fresh extraction of the ZIP.

## Automatic workspace

`alearn start --topic "<subject>" --goal "<practical goal>"` creates an offline
`index.html` roadmap, resources, and module folders with `TASK.md`, `submission/`,
`checks/`, and `feedback/`. The returned `learning_workspace` paths locate them.
This works for any topic. No GitHub account or web server is needed.

The host skill researches and adapts the curriculum, writes a plan, then passes
`--roadmap-file roadmap-plan.json`. A plain start creates a provisional first-task
shell marked `needs_curriculum_research`, not an invented full curriculum.

Plan shape:

```json
{"sources": ["https://roadmap.sh"], "status": "proposed", "nodes": [
  {"id": "first-project", "title": "First project", "task": "State an actionable task and acceptance criteria here.", "requires": []}
]}
```

The learner writes files in `submission/`; the agent verifies them, saves feedback,
and records evidence in the session ledger. HTML is an initial plan snapshot;
opening tasks does not mark them complete. Later sessions reuse existing work.

The primary learning interface is generated in `workspace/frontend/` as a React + Vite project with roadmap and lesson components. Run `npm install` and `npm run dev` there. Coding practice stays in the IDE under each module’s `submission/`. Include a `lesson` string in each roadmap node for learning content. The UI currently reads a plan snapshot; verification continues through the agent and runtime.

## NotebookLM CLI connection

The connector wraps the installed external CLI, using its existing authentication.
It adds no Python package dependencies and never stores authentication cookies.

```bash
./alearn notebooks
./alearn notebooks --json
./alearn notebook-select --session <SESSION_ID> --notebook-id <FULL_NOTEBOOK_ID>
./alearn notebook-sources --session <SESSION_ID>
# Or select during creation:
./alearn start --topic "<subject>" --goal "<goal>" --notebook-id <FULL_NOTEBOOK_ID>
# Explicit offline mode:
./alearn start --topic "<subject>" --without-notebooklm
```

Learning starts list notebooks by default and return `notebook_selection` when no
selection has been made. Select a full ID from the list; the runtime never chooses
a notebook based on its title. The selection is session-scoped, persisted in
`notebooklm.json`, and reflected in React's roadmap data. It never changes the
external CLI's global active notebook. `--notebook-cli nlm` selects the alternative
provider. Failures distinguish missing CLI, authentication, timeout, and malformed
responses; they never masquerade as an empty account.

The adapter implements notebook listing, binding, and source enumeration. The host
agent reads selected sources and develops the learning plan; notebook selection
alone does not import a curriculum or establish learner mastery. See the upstream
[notebooklm-py CLI reference](https://github.com/teng-lin/notebooklm-py/blob/main/docs/cli-reference.md).
