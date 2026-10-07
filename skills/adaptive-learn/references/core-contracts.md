# Core Contracts

## Kernel contract

The kernel owns only:

- interaction routing;
- session lifecycle;
- append-only logging;
- projection rebuild;
- policy orchestration;
- adapter discovery.

It does not own domain truth, domain validators, domain-specific safety, or representation content.

## Ledger contract

Records are append-only. No component silently edits prior records.

Each record has:
- stable id;
- timestamp;
- session id;
- record type;
- payload;
- provenance.

## Projection contract

`learner-evidence.json` is a derived view. Deleting it must not lose information; rebuilding it from the ledger must restore the same logical state.

## Diagnosis contract

Diagnosis is not a score and not a learner identity. It is a temporary explanatory object used only when it changes the next action.
