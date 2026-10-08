# Changelog

## Unreleased

### Follow-up learning and persistence audit
- Verify ledger record shape, session identity, sequence and hash chain together; reject writes to damaged journals while preserving their valid prefix and original bytes. `next` reports `repair_ledger`; inspection rebuilds evidence.
- Enforce evidence-format ceilings when reading, exclude future/malformed evidence, and reject invalid numerical scores instead of silently clamping them into successful observations.
- Isolate cross-session reports by learner and topic, sort by actual UTC time, and diagnose unresolved relapses after earlier successes. Only failing sessions count toward persistent failures.
- Fingerprint executable artifacts/checks and combine replay identities with named attempts so renamed/revised tasks cannot manufacture independent trials or erase assistance.
- Add `rubric-check` for attributed criterion judgments bound to the exact submission, with excerpt/rationale validation and critical-criterion gates. This deliberately remains medium self-report evidence, not automatic semantic verification.
- Add actionable `request_assessment` and `wait_for_review` states, concrete due-review items, workspace symlink confinement, and unknown outcomes for executor failures without a completed correctness check.


### Learning mechanism audit fixes
- Listener term coverage now produces diagnostic observations with unknown correctness, never strong evidence. Legacy listener evidence is excluded consistently from projection, BKT, cross-session aggregation and diagnosis without mutating ledgers.
- Split lesson advancement from mastery. `next` rebuilds its view from the verified ledger, reopens failed nodes with `feedback`, requests `change_approach` after three unresolved failures, and returns `consolidate` until current mastery is established. Placeholder roadmaps request research instead of teaching a dummy node.
- Verifier CLI and Python API accept attempt identity and support provenance; workspace checks default to unknown independence. Recorded help persists across rechecks of the same attempt. Named-task revisions do not accumulate independent BKT observations.
- BKT now decays from the last observation through now, orders dated observations, excludes future/undated records, validates parameters, and requires an actual 24-hour gap for delayed demonstrations. Strong evidence requires an explicit correctness check.
- Spacing grows only on due independent recalls; massed practice and partial/assisted work cannot postpone review. The 48-hour flag is documented as a product reminder and the scheduler as an uncalibrated heuristic, not trained HLR/FSRS.
- Added adversarial and end-to-end regression coverage for negation, false completion, failures after success, support inheritance, tampered projection inputs, repeated tasks, time handling and valid course completion. Updated older fixtures to declare their verification metadata and use unique evidence IDs.
- This supersedes the earlier Unreleased references to listener-based mastery and a single pass meaning course mastery.

### Multi-language mastery + agentic flow fixes (found by a 2-agent real-case run)
- A live OS↔learner simulation (Python-senior learning Kotlin/Android) exposed that a non-Python code node could never reach mastery: `verify-python` is Python-only and `run-exercise` executed code but recorded no evidence, so the agentic driver stalled on node 1. Fixed with `SessionKernel.verify_exercise` + `alearn verify-exercise`: runs ANY supported language through the execution backend and records evidence under the same trust rules as `verify-python` (executed AND checked (`--expected-stdout`) AND passing → strong, unassisted, mastery-eligible with provenance `exercise_verifier`; anything else → observation, never strong). Python/JS/Go/Java/… now reach mastery wherever a toolchain/container exists; a missing toolchain degrades to an honest observation, never a fake pass.
- `SessionKernel.set_roadmap` + `alearn set-roadmap`: attach/replace a roadmap on an existing session, so the agentic flow can gather intake FIRST and build the authoritative roadmap AFTER (the driver's `build_roadmap` step is now actionable). No-delete: an existing workspace is archived (`workspace-prev-*`), never dropped.
- Driver advance gate corrected: the next node unlocks on one STRONG, unassisted, verified success (projection), not on full BKT mastery — deep mastery (BKT p≥0.95 across scopes + transfer) and long-term retention stay with `mastery` and spaced review/cards, so the learner is never blocked from progressing by the retention bar.

### Authority-driven roadmap source (resource-independent)
- New `runtime/roadmap_source.py` + `alearn roadmap-source`: the roadmap path comes from an authoritative curriculum, not from whatever the learner uploaded. Programming topics resolve to the roadmap.sh backbone (a known slug, else its index); every other field resolves to an expert-research plan (university syllabi, standards bodies, recognized expert curricula). Returns a fetch plan (via Agent-Reach or web) and makes `resource_independent` explicit: learner/NotebookLM uploads ground the lessons, they do not define the sequence. Word-boundary matching avoids short-keyword false positives. The agentic driver's `build_roadmap` step and SKILL.md now route through it.

### Agentic tutor driver + memory cards (subject-neutral)
- New `runtime/driver.py` + `alearn next`: the OS now runs as a deterministic state machine that tells the host agent the single next step (`intake` → `build_roadmap` → due `review_capability`/`review_cards` → `teach` → `verify` → `complete`). The agent loops `alearn next`, executes one action, records it, repeats — an autonomous, auditable tutor where the LLM only does the parts it must (explaining, grading free-text/voice via listener-check) and advancement is decided by the OS from verified hash-chained evidence, never self-declared. Convention: `capability = node_id` binds evidence to roadmap nodes. SKILL.md documents the loop.
- New `runtime/memory_cards.py` + `alearn add-card` / `scaffold-cards` / `review-card` / `cards-due`: subject-neutral spaced-repetition flashcards. `scaffold-cards` derives card stubs from a roadmap node (title + pitfalls); due timing reuses the half-life scheduler (a new card is due immediately, then spaces out with recall). Cards are a study aid, never mastery evidence. Stored per session in `cards.json`; creation/review logged to the ledger.

### Study scheduling (subject-neutral)
- New `runtime/study_plan.py` + `alearn study-plan`: turns a roadmap into a dated weekly study schedule from a time budget (days/week, minutes/session, minutes/node, optional preferred weekdays, start, deadline). Prerequisite order is preserved; an infeasible deadline is flagged with concrete advice. Topic-agnostic — works for any subject, not just code — complementing the spaced-review scheduler (`scheduling.py`), which answers the different "when to review" question. Reads the session's roadmap or a `--roadmap-file`.

### Agent-Reach source backend (optional, zero-dep)
- New `runtime/reach_cli.py` + `alearn reach-doctor`: optional adapter for Agent-Reach (github.com/Panniantong/Agent-Reach, MIT) so a host agent can pull learning material from the open web, YouTube, Reddit, GitHub and RSS — "learn from here instead of just reading a book" for any subject. Availability-only by design: it is not a JSON API, so the core never parses content, never fetches, never takes a dependency; `reach-doctor` reports a coarse per-platform status with no raw diagnostics echoed. The host agent fetches, then registers results as governed sources (`sources-register` / NotebookLM); epistemic + rights gates still apply. SKILL.md documents the workflow.

### Local learning web app (zero-dep)
- The pedagogical OS is now reachable over HTTP, not just code execution. `alearn serve` adds `POST /api/session/start`, `POST /api/route` (records the decision + a SystemTrace), `POST /api/verify-python` (runs the deterministic verifier and records evidence through the same shared path as the CLI), `GET /api/inspect?session=` (now including `ledger_integrity`) and `GET /api/observability?session=`. Still stdlib-only; storage stays the append-only file ledger (no SQLite, no database).
- `SessionKernel.verify_python()` is a new single source of truth for the verify-then-record evidence rule, shared by the CLI (`alearn verify-python`) and the HTTP API, so the two cannot drift on what counts as strong evidence.
- Auth is fail-closed by default: `/api` executes code, so `alearn serve` with no token refuses to start unless anonymous loopback is EXPLICITLY opted into with `--allow-anon` (loopback only). This stops a repo user from unknowingly running an auth-free code-execution API. Set `ADAPTIVE_API_TOKEN` for authenticated use; binding beyond loopback without a token is always refused.
- DNS-rebinding / CSRF guard for anonymous mode: because `/api/run` and `/api/verify-python` execute code, anonymous loopback requests must additionally carry a loopback `Host` header and a same-origin (or absent) `Origin`. This blocks a malicious web page from DNS-rebinding to the local port or CSRF-POSTing code to the executor, without adding any token friction. Token-authenticated requests are unaffected (a cross-site page cannot read the token).
- New split-screen workspace UI (`decks/workspace.html`, served at `/` by default): left pane = mentor routing + reasons + observability, right pane = Monaco editor + Run / Run&Verify + output, with the "only executed + checked + passing = strong evidence" rule shown in the UI. Monaco loads from CDN in the browser (no build step); it degrades to a plain textarea if the CDN is unavailable. The old `academy.html` catalog demo is still available via `alearn serve --ui`.

### Source governance: Epistemic Gate
- Added the Epistemic Gate (`runtime/source_epistemics.py`, `alearn source-trust`) — the "can I trust this source?" half of source governance that the design named but the code only had as a string constraint. It scores a source's credibility tier (primary/official/peer-reviewed → high; practitioner/docs → medium; forum/blog/user-generated/ai-derived → low) and evaluates freshness against the claim's volatility (`stable` never expires, `slow` ~5y, `volatile` ~1y, `breaking` ~30d). A stale source is flagged for re-verification regardless of tier; a low-tier source needs 2+ independent corroborators or human review; retracted and un-provenanced AI-derived sources are rejected/held. Unknowns never silently become "trusted" (fail-closed).
- `source_gate(epistemic, rights)` combines the Epistemic Gate with the existing Rights Gate (deny > review > allow): a source is usable without a human only when it is both trustworthy AND permitted. `alearn source-trust --operation ...` returns the combined decision.

### Ledger integrity verification
- Exposed the ledger hash-chain check as an operator command: `alearn ledger-verify --session` runs `integrity()`, writes a dated receipt (`ledger-integrity.json`), and exits non-zero when the chain is broken so a cron/CI job fails loudly. `--cadence-hours N` skips re-verification while a receipt is fresh (the periodic `chain_verify_cadence` the design calls for); `--force` overrides it. `inspect` now reports `ledger_integrity` inline.

### System observability
- Wired the previously-dead `SystemTrace`: every `alearn route` now records a trace in the ledger, `alearn trace` logs a full turn (route, pedagogical intent, model calls, tools, verifier result, tokens, latency, failure/fallback), and `alearn observability --session` aggregates them (`summarize_traces`). Observability reads `verified_records()`, so a tampered tail cannot pad the report.

### Evidence trust
- Self-reported evidence can no longer prove mastery. `alearn evidence` (a bare declaration by the agent or learner) is stored with `verification: "self_report"` and capped at `medium` (`format_semantics.reasons` records `self_reported_ceiling:medium`; the requested strength is kept).
- `counts_as_strong(e, provenance=None)` now also rejects evidence whose payload is marked `self_report` or whose ledger provenance source is `cli_evidence_entry`. Projection, mastery, learner model and diagnostician pass the record provenance, so strong self-reports already in existing ledgers stop counting as strong. No ledger data is changed or removed.
- Strong evidence comes only from runtime-checked paths: `verify-python`, `workspace-check`, `listener-check`.
- SKILL.md step 5 now routes checks through those commands; README updated to the v0.6.0 feature set, including an evidence-trust table and a known-limitations section.

### Subject-neutral wording and checks
- The skill teaches any subject; general instructions no longer assume code. SKILL.md (checks chosen by kind of work for any [subject], Checker mode, practice location, tool/environment prerequisites), README, the generated workspace README and the React `Lesson` panel now describe practice work in whatever form the task requires.
- `workspace-check`: a `checks/check.json` with a non-executable `kind` (anything other than `code`/`fastapi`) returns `not_runtime_checkable` with directions to `listener-check` or rubric grading, instead of failing as `unknown_language`. The `no_check_spec` message gives the same directions. Neither records evidence.

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
