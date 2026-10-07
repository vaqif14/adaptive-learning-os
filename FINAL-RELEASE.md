# Final Integrated Release — Adaptive Learning OS v0.6.0

This ZIP consolidates the complete v5.x design line into one self-contained package.

## Integrated design families

### Learning science / evidence
- learning vs immediate performance;
- retrieval/generative evidence;
- support provenance;
- transfer/delayed evidence;
- novice/expert adaptation;
- no fake learning-style, brain-age, IQ, or ZPD precision.

### Meta-style interaction semantics
- Study Intent Catalog;
- compression, planning, critique, decomposition, interpretation, retrieval, simulation, delivery;
- Compression ≠ Mastery.

### Google / production patterns
- Challenge Gate / prove-it-first;
- generalized performance/artifact evidence;
- competence graphs/lifecycles through domain adapters;
- tool governance and system observability;
- reusable workflow/playbook thinking represented as non-mastery outputs.

### Stanford / human-centered AI
- Cognitive Delegation Gate;
- Protected Cognition contract;
- AI-use policy fields;
- AI as augmentor rather than silent replacement of target reasoning.

### Scott Young-style safeguards
- Curriculum Authority Gate;
- Practice Coverage;
- verifiability-by-construction preference.

### Habr / productivity anti-pattern correction
- explicit learning vs delivery goal mode;
- task completion never silently becomes learner capability.

### Source/IP governance
- epistemic quality separated from legal/rights usability;
- operation-specific source permissions;
- provenance preservation for AI-derived content;
- confidential user material never promoted to global corpus automatically.

### Accessibility / assessment integrity
- accessibility support separated from hints/cognitive delegation;
- process/live/oral/deterministic evidence preferred over AI-detector assumptions.

### Language / simulator fidelity
- multimodal domain requirements;
- authentic-environment requirement;
- simulation fidelity caps on transfer claims.

## Verification status

The release must pass:

```bash
python skills/adaptive-learn/scripts/validate_package.py
python -m unittest discover -s tests -v
```

The distributed ZIP is verified again after fresh extraction.


## v0.6.0 source orchestration

The runtime now treats source material as two classes: **available** and **missing**. Learner-supplied material is optional. By default (`adaptive_hybrid`), available material is used first and missing capability coverage is resolved autonomously through authoritative open-source discovery. Source provenance, freshness, and rights are checked before persistence/reuse. If a critical gap remains because the best source is paid, private, or inaccessible, the runtime emits a curated acquisition recommendation and asks the learner only then. Consumer NotebookLM is optional/manual; no Enterprise dependency exists.
