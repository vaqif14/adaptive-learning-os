# CLI-first staged generation

New CLI sessions start with local state and a suggested next action, not HTML.
The order is conversation/CLI goal clarification → brief captured performance
diagnostic → readable governed resources → personalized roadmap HTML → explicit
approval of that version → personalized teaching workspace and lesson authoring.

`start --roadmap-file` stores candidate data; it does not render that plan. The
generic `set-roadmap` path cannot bypass the guarded course flow in a CLI-first
session. The direct Python API retains its labelled `legacy_workspace` default
for compatibility; callers adopting the staged flow use `defer_workspace=True`.
Course initialization enforces strict policy for a CLI-first session even if an
API caller omits `require_intake`. Existing legacy courses are not silently
migrated or erased. Existing strict-intake courses use the corrected generation
guards without rewriting their learner answers.

## Actual clarification and diagnostic decisions

Use `course target --file <target.json>` for a verified certification target:
official name, exam code, official HTTPS verification URL/date, objectives, and
explicit version or an unpublished-version note. Ambiguous certification language
cannot be relabelled as generic learning to bypass this requirement. The host
must check the official source and ask the learner which target they intend.
These metadata fields do not prove a webpage was genuinely checked on their own.

Then record the learner’s real confirmation through `course goal-clarify --file
<confirmation.json>`, for example a **synthetic test fixture**:

```json
{"kind":"learning","statement":"TEST FIXTURE: bu konkret məqsəd və çərçivəni təsdiqləyirəm."}
```

Certification uses `kind: certification` after exact target selection. If a
diagnostic was skipped, include `accept_provisional_skip: true` only after the
learner has understood and explicitly accepted that the plan is provisional and
no ability result exists. A vague old skip reason is preserved but does not supply
this consent. Confirmation binds to current goal, target, background and skip;
changes require fresh clarification. Never manufacture learner assent or answers.

Two separately captured, scoped and judged diagnostic tasks can resolve the brief
diagnostic stage; they do not measure intelligence, a fixed learning style, broad
mastery or calibrated learning probabilities. Assistance stays visible.

## Generation and source gates

`course propose-roadmap` refuses before clarification/diagnostic resolution and
source readiness. Every selected expected resource must have a valid manifest,
readable cached UTF-8 text whose bytes match its committed hash, and a usable current
status. Every proposed node must name valid source keys. No missing book, catalog
title, forged complete flag or absent cache is silently treated as grounded content.
Paid acquisition, new notebook/source writes and material transmission still need
the learner's authorization; missing resources remain explicit.

The generated roadmap binds goal confirmation and source identity/stable text
versions in `generation_provenance`, which participates in roadmap versioning.
If a valid source changes, even when old lesson quotes still match, the old proposal
must be refreshed and approved again. Re-grounding a lesson is not new roadmap
approval. Direct teaching and lesson API access also refuse stale generation bases.

A fresh proposal creates only roadmap data and clickable HTML preview. It does not
create new module TASK files, submissions or personalized React workspaces. Exact
approval atomically journals approval, session metadata and the staged teaching
workspace install. A failed preparation cannot expose teaching assets or record
approval. Journal recovery can finish a committed install after interruption.
Revision retains old submissions, checks, notes, resources and historical artifacts.

## Optional web intake

`course serve --intake` explicitly serves the generic **Tanışlıq** shell and later
roadmap preview. It is not a generated personalized course. Before a real roadmap
version exists, there is no placeholder roadmap/course navigation; stale bookmarks
return to intake. It shows unresolved target/clarification/source questions and never
auto-completes them. Normal `course serve` refuses until exact-version approval.
The application exam lock still outranks all routes. Local owner control prevents
any claim of cheatproof testing.

An already created placeholder workspace can remain on disk as a preserved legacy
artifact. The corrected app hides it; no progress or learner files are deleted just
to change the presentation. Optional web intake is supplementary to the requested
conversation/CLI-first workflow. It must be described as intake, not final delivery.

NotebookLM remains the existing external CLI service, not an offline or replacement
hosted API service. Functional tests and synthetic consent fixtures show the workflow
guards; they do not prove human learning efficacy or calibrated semantic grading.
