# Cognitive Delegation & Protected Cognition

The system must distinguish work the learner must perform from work AI may safely delegate.

## Contract

```yaml
target_capability: design_scalable_backend
protected_cognition:
  - identify_constraints
  - generate_options
  - compare_tradeoffs
  - justify_decision
delegable_work:
  - formatting
  - diagram_rendering
  - citation_formatting
independence_required: true
```

AI roles:

```text
inform, model, probe, scaffold, critique, verify, delegate, replace
```

In `learn` mode, `replace` or `delegate` must not silently perform protected cognition needed to establish independence.

Accessibility accommodations are not cognitive delegation. Text-to-speech, speech-to-text, display changes, or equivalent access supports must not be counted as hints merely because they alter the interface.
