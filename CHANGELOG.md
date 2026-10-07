# Changelog

## 0.6.0 — Enterprise hardening + graphify integration

Driven by a 6-lens expert review (architecture, pedagogy, security, correctness, QA, defensive-sec). No behavior was removed; every change ships with tests.

### Security / safety
- Path traversal closed: `runtime/safety.py` validates session ids (`^sess_[0-9a-f]{16}$`) and confines every id/file arg with `is_relative_to`; CLI file inputs capped and workspace-confined.
- Ledger is now crash-safe and tamper-evident: atomic locked appends, newline guard, `ensure_ascii` (U+2028/U+0085 can no longer corrupt a line), a tolerant reader that skips torn lines, a hash chain + `integrity()`.
- Atomic `write_json` (temp + os.replace) with 0600/0700 permissions.
- Verifier rewrite: learner code runs under a separate trusted harness (a learner `SystemExit` can no longer skip trusted tests), builtins are an allowlist (open/eval/__import__ unreachable), output is capped, the child runs in its own process group, and resource limits degrade instead of crashing (macOS `RLIMIT_AS` fix). Unchecked runs are never recorded as strong evidence.
- Gates fail closed: delegation no longer lets an "accessibility" self-label launder `replace`; overlap is case-insensitive; tool-gate consults the capability registry and denies unknown tools; `--permit`/`--deny` are mutually exclusive; prohibited rights hard-deny.
- Central CLI error handler: domain errors return a clean JSON message, not a traceback.

### Correctness
- Routing rebuilt: NFKC + Turkic-aware casefold, word-boundary matching (no more "review" in "preview"), learn-verbs beat quick hints, Azerbaijani fixes ("məni yoxla" reaches practice, "İmtahan ver" -> assess). `high_stakes` hoisted above all intents.
- One strategy-driven source policy (the two overlapping classes are unified); `strict_closed_world` no longer plans discovery; strict discovery-JSON typing.
- Adapter matching is token/phrase based; contract `from_dict` guards list/enum fields; CLI numeric bounds.

### Evidence loop + pedagogy
- Projection honors support provenance and scope, deduplicates, and tightens uncertainty clearing (`counts_as_strong`).
- Evidence loop closed: `evidence_bridge` derives policy inputs from the projection (`policy --from-session`); cross-session `learner_model` + `learner-state`; decisions log their inputs.
- New learner science: BKT mastery estimate with forgetting (`mastery.py`, `alearn mastery`), half-life-regression spaced review (`scheduling.py`, `alearn review-due`), a graded `feedback` move and a wheel-spinning `change_approach` guard, confidence ratings.

### Packaging
- `pip install .` now ships the runtime (`alearn_skill` package) and `alearn` works outside the repo; `--version`; versions unified at 0.6.0.

### Knowledge graph (graphify)
- Optional graphify `graph.json` provider: `knowledge_graph.py` + `graph_bridge.py` + `alearn graph-curriculum|graph-coverage|graph-anchors`. Fills the previously-dead `dependency_graph_available` curriculum path. Core stays dependency-free.


## 0.5.5 — Final integrated v5.x release

Integrated all design conclusions gathered after v5.2.2 without expanding the thin kernel into a monolithic tutor.

### Interaction semantics
- Study Intent Catalog now includes quick reference, compression, planning, learning, decomposition, co-production, critique, question interpretation, retrieval practice, simulation, and delivery.
- Added explicit goal modes: `learn`, `perform_with_assistance`, `deliver_artifact`, `quick_reference`.
- Preserved `Compression ≠ Mastery` and delivery-output ≠ learning-evidence invariants.

### Cognitive delegation
- Added Protected Cognition / Cognitive Work Contract.
- Added AI cognitive roles: inform, model, probe, scaffold, critique, verify, delegate, replace.
- Learning mode can block AI from replacing protected cognition while allowing non-target delegation.
- Accessibility support is explicitly separated from pedagogical support and cognitive delegation.

### Challenge / performance
- Added capability-specific Challenge Gate / prove-it-first semantics.
- Added generalized Performance Evidence and artifact metadata contracts.
- Challenge passes never create global learner-level labels.

### Domain / curriculum / practice
- Added Curriculum Authority Gate: LLM adapts authoritative curricula/dependency graphs before inventing sequences.
- Added Practice Coverage planner to prevent repetitive LLM drill loops.
- Expanded domain manifests with competence graphs, modalities, authentic-environment requirements, challenge strategy, curriculum strategy, governance, and practice dimensions.
- Added language-learning reference adapter.

### Source governance
- Added separate epistemic vs rights gates.
- Added source-operation semantics for read/quote/summarize/transform/index/retain/redistribute/fine-tune/train.
- Added AI-derived provenance requirement and explicit unknown-rights handling.

### Simulation / multimodality
- Added Simulation Fidelity evidence caps.
- Added M4 contract-only primitives for artifact workspace, audio dialogue, source comparison, and scenario simulation.
- Browser/HTML/JS rendering is still intentionally absent.

### Production governance
- Added deterministic Tool Policy Gate.
- Added system trace/observability contract.
- Added tool-capability registry.
- Kept invasive learner telemetry out of mastery/diagnosis authority.

### Assessment integrity
- Expanded evidence formats for artifact/scenario/oral/live performance.
- Added AI-generated-output non-mastery invariant.
- Preserved question-interpretation gate before misconception diagnosis.

### Packaging / validation
- Self-contained runtime, manifests, schemas, docs, adapters, tests, and CLI.
- Final ZIP is tested from a fresh extraction.

## 0.5.2.x

- Added Study Intent Catalog, question interpretation gate, review-item representation policy, resource/deadline-aware contract, scenario-simulator contract, deterministic Python verifier, anchor probes, adaptive progression, diagnosis restraint, weak-context latency semantics, and self-contained package validation.

## 0.5.1

- Frozen thin-kernel architecture with append-only ledger, rebuildable learner evidence projection, domain adapters, support provenance, progressive routing, and no browser runtime in M0–M3.


## v0.5.5 source orchestration

The runtime now treats source material as two classes: **available** and **missing**. Learner-supplied material is optional. By default (`adaptive_hybrid`), available material is used first and missing capability coverage is resolved autonomously through authoritative open-source discovery. Source provenance, freshness, and rights are checked before persistence/reuse. If a critical gap remains because the best source is paid, private, or inaccessible, the runtime emits a curated acquisition recommendation and asks the learner only then. Consumer NotebookLM is optional/manual; no Enterprise dependency exists.
