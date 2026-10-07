# Domain Adapters

## Principle

Domain-general kernel, domain-sensitive adapters.

An adapter declares:
- matching signals;
- truth modes;
- source priority and freshness policy;
- feedback latency;
- ambiguity;
- risk profile;
- available validators/tools;
- valid evidence types.

A new adapter must not modify router, ledger, projection, or policy core.

Unknown domains fall back to `generic-research`, which preserves uncertainty and asks for/source-discovers only what is needed.
