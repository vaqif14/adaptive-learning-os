# Teaching Policy

## V1 policy style

Use deterministic gates around an LLM's proposed pedagogical move.

The runtime should reject or downgrade a proposed move when it conflicts with:
- explicit user interaction mode;
- missing prerequisite schema;
- support provenance;
- high-stakes safety;
- a domain adapter constraint;
- evidence scope.

## Utility discipline

Use qualitative decision factors rather than fake calibrated probabilities:

```text
learning value
+ information value
+ transfer relevance
- learner friction
- time/cost
- dependency risk
- domain/safety risk
```

The system need not compute a numeric score unless validated data later justifies one.
