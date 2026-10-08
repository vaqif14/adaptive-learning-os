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
- Deterministic Python verifier for low-risk snippets, plus language-agnostic execution backends (see [Verification and execution](#verification-and-execution)).
- Evidence-format ceilings and compression ≠ mastery.
- Self-reported evidence ceiling: only runtime-checked evidence can be strong (see [Evidence trust](#evidence-trust-what-counts-as-strong)).
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

These M4 primitives remain **contracts only**: there is no runnable simulator or audio-dialogue engine yet. Learning starts now generate a separate offline HTML roadmap and task workspace automatically.

### v0.6.0 runtime additions

| Area | What runs | CLI |
|---|---|---|
| Ledger integrity | Hash-chained, locked, crash-tolerant appends; readers use only the verified chain prefix. Operator check with dated receipt + cadence; exit 2 if broken | `ledger-verify`, `inspect` |
| Source trust (Epistemic Gate) | Credibility tier + freshness-by-volatility; combines with the Rights Gate (deny > review > allow) | `source-trust` |
| Observability | Live `SystemTrace` per route + manual turn logging; per-session aggregation over the verified chain | `trace`, `observability` |
| Execution | 12 languages: python, javascript, typescript, go, java, kotlin, rust, c, cpp, ruby, php, sql. Local toolchain or per-run container | `languages`, `run-exercise`, `verify-fastapi` |
| Workspace checks | Reads `submission/`, runs `checks/check.json`, writes `feedback/`, records evidence | `workspace-check` |
| Mastery | BKT estimate decayed to now; qualified breadth + transfer; latest failure reopens assessment | `mastery` |
| Spaced review | Due-time-aware spacing heuristic; massed practice cannot extend intervals; 48 h reminder | `review-due` |
| Learner model | Cross-session, time-aware aggregation from verified ledgers | `learner-state` |
| Diagnosis history | Persistent failures, co-failure clusters, misconception tally | `diagnose-history` |
| Explain-back coverage | Lexical coverage only; correctness remains unknown pending contextual assessment | `listener-check` |
| Teaching modes | intake, roadmap, socratic, checker, listener, examiner | `modes`, `prompt`, `intake` |
| HTTP API + web app | Stdlib server (no deps). Session/route/verify/inspect/observability endpoints + split-screen Monaco workspace at `/`. Loopback runs auth-free; beyond loopback needs a token | `serve` |
| Knowledge graph | Optional graphify `graph.json` provider | `graph-curriculum`, `graph-coverage`, `graph-anchors` |

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
Epistemic Gate: Can I trust this source?   -> alearn source-trust
Rights Gate:    May I use this source this way?  -> alearn source-use
```

Both gates are enforced in code, not just named:

```bash
# Epistemic: credibility tier + freshness for the claim's volatility
alearn source-trust --source-id doc1 --source-type official \
  --publication-date 2015-01-01 --claim-volatility volatile
# -> status: stale  (a 10-year-old doc fails a volatile claim regardless of tier)

# Combined: trustworthy AND permitted (deny > review > allow)
alearn source-trust --source-id doc1 --source-type forum \
  --publication-date 2026-09-01 --operation quote
# -> status: review_required  (low-tier source needs corroboration)
```

Credibility tiers: primary/official/peer-reviewed → high; practitioner/docs → medium; forum/blog/user-generated/AI-derived → low (needs 2+ independent corroborators or human review). Freshness volatility classes: `stable` (never expires), `slow` (~5y), `volatile` (~1y), `breaking` (~30d). Retracted or un-provenanced AI-derived sources are rejected; unknowns never silently become trusted.

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

## Verification and execution

**Python verifier.** AST policy checks, isolated Python mode, temporary working directory, timeout, resource limits, a separate trusted harness, and optional expected output/trusted assertions. It is enabled per domain adapter and is a **best-effort local verifier**, not a hardened security sandbox.

**Execution backends** (`runtime/execution.py`). `LocalToolchainBackend` uses host toolchains for development. `ContainerBackend` is the production path: one container per run, `--network none`, read-only root, `cap-drop ALL`, non-root user. A language whose toolchain or image is missing returns an install/image instruction, never "unsupported". Arbitrary untrusted code belongs in `ContainerBackend`.

## Evidence trust: what counts as strong

Mastery breadth and transfer require **strong unassisted successes** with an explicit correctness check. Weaker graded results can inform uncertainty and feedback; lexical coverage cannot grade correctness:

| Evidence path | Ledger source | Max strength |
|---|---|---|
| `verify-python` (executed + correctness checked) | `python_verifier` | strong |
| `workspace-check` (executed + checks passed) | `workspace_check` | strong |
| `verify-exercise` (executed + correctness checked) | `exercise_verifier` | strong |
| `listener-check` (lexical coverage only) | `listener` | observation; no mastery credit |
| `evidence` (manual entry by agent or learner) | `cli_evidence_entry` | **medium** |

Manual entries are declarations, so they still feed the learner model at reduced weight but can never prove mastery. The ceiling is applied when the record is written (payload `verification: "self_report"`) and again when it is read (ledger provenance). That keeps ledgers written before this rule from being trusted retroactively.

## Learning decisions and retention

`next` separates **ready_to_advance** (one verified independent pass, no newer failure) from **mastered** (current BKT threshold, qualified breadth, transfer/delayed evidence, and a current qualified success). A completed lesson sequence without mastery returns `consolidate`; it never claims the course goal was met. One failure requests `feedback`; three consecutive unresolved failures request `change_approach`. Decisions rebuild from the verified ledger, not a potentially stale projection file. A placeholder roadmap still returns `build_roadmap` until a real plan is attached using `set-roadmap`.

`mastery` accounts for elapsed time through the current instant. Undated and future observations cannot award current mastery. A `delayed_independent_performance` label needs at least 24 hours since the previous graded touch. These parameters are operational defaults, not empirical guarantees of learning.

Review spacing grows only after an independent recall at or after its due time. Early repeated successes, partial answers and assisted answers do not postpone the deadline. Lapses shorten it. The 48-hour flag is an operational reminder, not a scientifically established apply-or-lose law. The scheduler is a transparent heuristic, not trained HLR or FSRS.

### Recording assistance

`verify-python` and `verify-exercise` accept `--attempt-id`, `--hints-count`, `--worked-example-shown`, `--ai-direct-answer-revealed`, and `--conceptual-scaffold`. The Python HTTP endpoint accepts `attempt_id` and the equivalent `support_provenance` object. `workspace-check` reads those fields from `checks/check.json`; omitted independence defaults to `unknown`.

Keep an attempt ID stable across revisions of the same task. The runtime merges recorded assistance monotonically for that capability and attempt; an empty support object cannot erase earlier help. Use a new ID only for a genuinely new task. Without an ID, support is conservatively inherited within the capability's implicit attempt. Rechecking a named task replaces its result in the BKT input instead of adding independent trials. The host still must honestly record task novelty, scope and assistance; code execution alone cannot establish who wrote the answer.

Legacy listener records are excluded from learning judgments without rewriting the ledger. Older records lacking `correctness_checked: true` no longer count as strong. Rebuild a projection with `project` when inspecting a previously cached view.

## Assessment and history safeguards

- `rubric-check` records attributed criterion scores, reasons and excerpts bound to the submission's SHA-256. Critical failed criteria block an overall pass. It is a validated assessment report, not an automatic semantic grader; evidence remains medium. See [the runnable format example](skills/adaptive-learn/references/rubric-assessment.md).
- `next` returns `request_assessment` after ungraded lexical coverage, `wait_for_review` with a `resume_at` time when delayed evidence is premature, and `repair_ledger` if the journal is damaged. A due capability review includes the actual review items. Hosts must stop and wait on these external requirements rather than loop.
- `learner-state` and `diagnose-history` reject mixed learners or mixed topics unless selected with `--learner-id` and `--topic`. Capability IDs should retain the same meaning within a topic. Records are ordered by UTC timestamps; invalid/future evidence is excluded. Persistent failures count unresolved failures after the latest success and only sessions that actually failed.
- Exact executable artifact/check replays are fingerprinted. Renaming an attempt cannot turn a replay into independent trials or erase recorded assistance. Named revisions and artifact replays share one trial in the knowledge model.
- Ledger readers verify record shape, session identity, sequence and the hash chain. Appends reject damaged journals before writing; learning actions request repair, preserving original data. Inspection rebuilds its projection. Files outside a module cannot enter workspace checks through symlinks, and executor failures without a completed check remain unknown outcomes.

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
python3 skills/adaptive-learn/scripts/validate_package.py
python3 -m pytest -q
```

CI (`.github/workflows/ci.yml`) runs the suite, the validator and a wheel smoke test on ubuntu and macOS × Python 3.11–3.13.

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

The primary learning interface is generated in `workspace/frontend/` as a React + Vite project with roadmap and lesson components. Run `npm install` and `npm run dev` there. Practice work, in whatever form the task requires (code, text, worked solutions, notes), stays in each module’s `submission/`; code is edited in the IDE. `workspace-check` runs executable work only; a module whose `checks/check.json` declares a non-executable `kind` gets `not_runtime_checkable` with directions to `listener-check` or rubric grading. Include a `lesson` string in each roadmap node for learning content. The UI currently reads a plan snapshot; verification continues through the agent and runtime.

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

## Known limitations

- **Single operator.** The HTTP API has one shared Bearer token: no user accounts, roles or tenant isolation. Hosted multi-user use needs those first.
- **Host-agent dependence.** Gates and modes take effect when the agent calls the runtime; nothing forces it to. The evidence-trust rule limits the damage: skipping the checks cannot produce mastery.
- **Non-code domains.** `listener-check` reports lexical coverage and requests semantic review; it cannot certify understanding, including for paraphrases and negated claims. Rubric-graded work recorded through `evidence` stays at medium until an authenticated grader path exists. The runtime must not manufacture completion when that verifier is unavailable.
- **Uncalibrated learner model.** BKT parameters are engineering defaults (`p_learn` 0.15, `p_slip` 0.1, `p_guess` 0.2), not fitted to learner data.
- **No empirical validation.** The design follows retrieval-practice and spacing research; this implementation has no outcome study or A/B data yet.
- **Container coverage.** The container path is smoke-proven for python and node; other language images are configured but not yet exercised in CI.
- **M4 primitives** (`scenario_simulator`, `audio_dialogue`, …) are contracts only.
