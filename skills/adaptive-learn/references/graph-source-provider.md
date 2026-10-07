# Optional Knowledge-Graph Source Provider (graphify)

The runtime can consume a **graphify** (`github.com/Graphify-Labs/graphify`)
`graph.json` as an optional, swappable source provider. It is never required and
the core never imports graphify — only the portable `graph.json` artifact is read
(`runtime/knowledge_graph.py`). This satisfies the loose-coupling rule in
`source-governance.md`: providers plug in, they are not hard-wired.

## Why

The shipped Curriculum Authority Gate (`runtime/curriculum.py`) accepts a
`dependency_graph_available` flag, but nothing in the runtime ever produced a real
dependency graph to set it. graphify fills exactly that hole: deterministic,
local AST extraction yields a validated dependency structure the LLM *adapts*
instead of inventing a sequence from nothing.

## Build the graph (outside the runtime)

```bash
# code-only: pure local AST, no API key, nothing leaves the machine
graphify extract <corpus> --code-only --out <dir>
# mixed corpus (docs/PDFs) uses the host agent's model for the semantic pass
graphify extract <corpus> --out <dir>
# → <dir>/graphify-out/graph.json
```

## Consume it

```bash
alearn graph-curriculum --graph <dir>/graphify-out/graph.json --target "EvidenceProjector"
alearn graph-coverage   --graph <dir>/graphify-out/graph.json --required-capability consensus --required-capability replication
alearn graph-anchors    --graph <dir>/graphify-out/graph.json --k 5
```

- `graph-curriculum` → prerequisite-first ordering + the Curriculum Authority Gate
  decision (`adapt_validated_dependency_graph` when the target resolves).
- `graph-coverage` → maps required capabilities onto graph nodes
  (covered / partial / missing) and runs the existing source-gap analysis.
  **Corpus coverage is not learner mastery.**
- `graph-anchors` → highest-degree concepts (god nodes) + communities as
  cold-start Anchor Probe candidates.

## Governance

- A graph built with a semantic (LLM) pass is `ai_derived` and is marked
  `requires_human_review` before reuse/retention (`graph_as_source_record`).
- A pure-AST (`--code-only`) graph is a deterministic transform of the learner's
  own corpus (`provenance == "deterministic_ast"`).
- Edge direction is read from graphify's `_src`/`_tgt` arc markers when present,
  so the dependency direction is never silently reversed.
