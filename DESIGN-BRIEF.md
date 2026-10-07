# Adaptive Learning OS v0.6.0 — Final Design Brief

## 1. Frozen core

> **Domain-general orchestration; domain-sensitive cognition, verification, representation, and safety.**

The kernel orchestrates evidence and decisions. New domains may add adapters, validators, sources, tools, modalities, competence graphs, safety rules, and authentic-environment requirements without rewriting the kernel protocol.

## 2. Minimal learner model

The runtime stores:

- demonstrated capabilities;
- uncertainties;
- tentative explanatory hypotheses only when decision-relevant;
- support provenance;
- transfer/delay evidence;
- task/performance context.

It does not store fake IQ, “brain age”, fixed learning styles, or pseudo-precise ZPD scores.

## 3. Interaction semantics

Learning is not one interaction mode. The Study Intent Catalog distinguishes compression, planning, teaching, decomposition, critique, question interpretation, retrieval practice, simulation, co-production, quick reference, and delivery.

Compression, plans, and AI-produced outputs never become mastery evidence by themselves.

## 4. Protected cognition

The runtime separates:

- target cognition the learner must perform;
- non-target work AI may delegate;
- accessibility support;
- pedagogical support;
- cognitive replacement.

If a user is in `learn` mode and AI would replace protected cognition required for independence, the policy reduces or blocks that delegation.

## 5. Evidence hierarchy without rigid staircase

Evidence scopes:

```text
supported_completion
independent_reproduction
near_transfer
far_transfer
delayed_independent_performance
```

These are distinct evidence claims, not compulsory sequential lessons. Strong learners may skip redundant reproduction if independent reasoning is already available and no full solution was exposed.

## 6. Challenge Gate

Fast-track requests or credible prior experience can trigger one authentic capability-specific challenge. Passing the gate skips redundant teaching for that capability only.

## 7. Curriculum Authority Gate

The LLM does not invent prerequisite order when a stronger curriculum/dependency source exists. If no authoritative structure exists, it researches, triangulates, and preserves uncertainty.

## 8. Domain adapters

Adapters define:

- truth modes;
- source hierarchy/freshness;
- competence graph;
- valid performance evidence;
- validators;
- challenge strategy;
- modalities;
- authentic environment requirements;
- practice dimensions;
- risk/governance rules.

## 9. Source governance

Epistemic quality and rights status are independent axes. Source operations are evaluated separately for read, quote, summarize, transform, local indexing, retention, redistribution, fine-tuning, and model training.

## 10. Performance evidence

“Artifact-Based Mastery” is generalized to **Performance Evidence** because not every domain produces a file artifact. A performance record may be an artifact, dialogue trajectory, decision trace, oral defense, or live execution.

## 11. Simulation fidelity

Simulation evidence is capped by fidelity. An AI scenario may support near transfer but does not automatically establish authentic-world far transfer.

## 12. Accessibility

Accessibility accommodations change access, not necessarily cognition. They must not be counted as hints merely because they alter presentation or input modality.

## 13. Practice coverage

Practice generation must vary contexts/surface forms and track under-covered dimensions to avoid LLM repetition loops.

## 14. Governance and observability

The runtime distinguishes learner telemetry from system telemetry.

Do not infer cognition from mouse/hover/scroll timing. Do record system traces such as route, model/tool calls, source IDs, latency, token use, verifier result, selected policy move, failure, and fallback.

Tool actions pass a deterministic policy gate before execution.

## 15. Browser/runtime restraint

M4 browser primitives remain declarative contracts only in this release. CLI/text is still the default representation. A future renderer must be deterministic, sandboxed, accessible, and evidence-aware.


## v0.6.0 source orchestration

The runtime now treats source material as two classes: **available** and **missing**. Learner-supplied material is optional. By default (`adaptive_hybrid`), available material is used first and missing capability coverage is resolved autonomously through authoritative open-source discovery. Source provenance, freshness, and rights are checked before persistence/reuse. If a critical gap remains because the best source is paid, private, or inaccessible, the runtime emits a curated acquisition recommendation and asks the learner only then. Consumer NotebookLM is optional/manual; no Enterprise dependency exists.
