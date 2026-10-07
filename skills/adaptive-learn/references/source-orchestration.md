# Source Orchestration — Two Material Classes

The runtime never assumes that the learner must provide a corpus before learning can begin.

## Material class A — available

Sources usable now:

- learner-supplied files or URLs;
- canonical resources already registered;
- lawfully discovered authoritative/public sources that pass source governance.

These sources are preferred and reused when relevant.

## Material class B — missing

Required capability coverage that is not supported well enough by current sources.

Default behavior is `adaptive_hybrid`:

```text
goal
  ↓
competence/curriculum requirements
  ↓
coverage against available materials
  ↓
missing/partial gaps
  ↓
automatic authoritative-source discovery
  ↓
epistemic + rights/freshness checks
  ↓
usable open source? ── yes ──► register/use it
        │
        no
        ▼
curated acquisition recommendation
        │
        ▼
ask learner only if critical access/purchase/private material is truly required
```

## Frozen rules

1. User material is optional, never a prerequisite for starting a session.
2. If materials exist, use them before redundant discovery.
3. Missing coverage triggers autonomous discovery by default.
4. Discovery prefers domain-appropriate official, primary, peer-reviewed, and authoritative sources.
5. Volatile claims require freshness checks.
6. Every discovered source retains provenance and rights state.
7. Public access is not blanket permission for retention, redistribution, or training.
8. If lawful/authoritative open coverage is insufficient, produce a ranked acquisition list; do not bypass paywalls or access controls.
9. Ask the learner for material only when the unresolved source is truly required and cannot be obtained lawfully by the runtime.
10. `strict_closed_world` is opt-in, not default.
11. Consumer NotebookLM is optional as a human-operated research workspace; no Enterprise connector is required or assumed.
12. Corpus coverage is not learner mastery evidence.
