# Source Governance

Two independent gates are required:

```text
Epistemic Gate — Can this source support the claim?
Rights Gate     — May this source be used for this operation?
```

## Frozen rules

- Public access is not blanket permission.
- Reading, quoting, summarizing, transforming, indexing, retention, redistribution, fine-tuning, and training are separate operations.
- Unknown rights remain unknown.
- Do not invent licenses or jurisdictional permissions.
- AI-derived summaries retain provenance to underlying sources.
- User-supplied private/confidential material must not enter a global corpus automatically.
- RAG/reference use is not equivalent to model training/fine-tuning rights.

This runtime is not a legal opinion engine. Unknown or high-risk rights states should be escalated for human/legal review.


## Default orchestration

Governance is applied inside an `adaptive_hybrid` source flow. Existing learner/canonical materials are used when available. Missing coverage triggers autonomous authoritative-source discovery; only unresolved critical closed/paid/private gaps are escalated as acquisition recommendations. The learner is not required to upload material every session.
