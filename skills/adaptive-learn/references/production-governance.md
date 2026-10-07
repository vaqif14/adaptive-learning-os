# Production Governance & Observability

## Policy gate

LLM proposes; deterministic policy authorizes; tools execute.

A tool request should be checked for:

- authorization;
- side effects;
- risk;
- source/data policy;
- resource/cost class.

## System observability

Record system traces such as:

- route and Study Intent;
- policy move;
- model calls;
- tools called;
- source IDs;
- latency/token usage when available;
- verifier results;
- failures and fallbacks.

This is system telemetry, not learner cognition telemetry.

Do not infer learner ability from mouse movements, scroll velocity, hover time, or network latency.
