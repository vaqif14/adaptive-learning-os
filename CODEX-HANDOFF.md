# Codex Engineering Handoff — Adaptive Learning OS v0.6.0

## Status

M0–M3 are the production candidate. M4 UI primitives are contract-only.

## Start here

1. Run `validate_package.py`.
2. Run the full deterministic unit suite.
3. Exercise all Study Intents.
4. Verify learning vs delivery behavior through Cognitive Delegation tests.
5. Create a Python session and verify executable evidence.
6. Create a language-learning session and inspect authentic-environment/modalities metadata.
7. Test Challenge Gate behavior; never promote a capability-specific pass to global expertise.
8. Delete the learner projection and rebuild it from the ledger.
9. Test Source Rights Gate with allowed, denied, and unknown operations.
10. Test accessibility support and confirm it does not automatically lower independence.
11. Test simulation fidelity caps.
12. Confirm learning starts generate a React + Vite frontend and offline HTML preview; coding submissions stay in the IDE. M4 simulation/audio primitives remain contracts only.

## Non-negotiable invariants

- Ledger is source of truth.
- Projection is rebuildable.
- Self-report is context, not mastery evidence.
- Compression/plan/AI-generated output ≠ mastery.
- Supported completion ≠ independent capability.
- Repetition alone does not trigger deep diagnosis.
- Question interpretation is checked before misconception inference when ambiguity is plausible.
- LLM hypotheses remain tentative and falsifiable.
- Deterministic claims use deterministic verifiers when available.
- AI must not replace protected cognition in learning mode.
- Accessibility support is distinct from pedagogical support.
- Challenge passes are capability-specific.
- Curriculum sequencing defers to authoritative structures when available.
- Simulation success is capped by fidelity/authenticity.
- Public source access does not imply unrestricted rights.
- AI-derived sources retain underlying provenance.
- Learner telemetry is minimized; system observability is explicit.
- React learning workspace is generated on start; browser code execution is not part of this workspace.

## Future M4

Implement only after real-trajectory acceptance:

- deterministic Primitive Registry renderer;
- browser Event Bridge;
- CSP/sandboxing;
- accessibility validation;
- answer-leakage tests;
- runtime provenance and trace capture.


## v0.6.0 source orchestration

The runtime now treats source material as two classes: **available** and **missing**. Learner-supplied material is optional. By default (`adaptive_hybrid`), available material is used first and missing capability coverage is resolved autonomously through authoritative open-source discovery. Source provenance, freshness, and rights are checked before persistence/reuse. If a critical gap remains because the best source is paid, private, or inaccessible, the runtime emits a curated acquisition recommendation and asks the learner only then. Consumer NotebookLM is optional/manual; no Enterprise dependency exists.
