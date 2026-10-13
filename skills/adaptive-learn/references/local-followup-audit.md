# Local follow-up audit — 2026-10-12

The confirmed checkout is the owner's local working copy,
origin `vaqif14/adaptive-learning-os`, HEAD `df88a0cf1eec3aa0e31d1eb504f24622b6376c72`.
Remote main was read again and still points to that commit. The checkout already
contained extensive uncommitted v7.1 work. This follow-up preserves it and tests
a separate source snapshot. Existing decision-log claims about earlier reviewers
and studies are historical records, not independently re-proven by this audit.

Four review perspectives were applied by the same host agent: learning science,
product/UX, runtime/data integrity, and assessment/security. They are analytical
roles, not independent reviewers or an external expert consensus.

## New confirmed defects and fixes

1. **Due reviews could be bypassed.** The next mission said review first, but direct
   `start_mission`, `start_exam`, help and lesson retrieval still allowed study.
   Red-first regressions reproduced this. The shared runtime now refuses teaching,
   practice and ordinary new exams with `review_required` while a current review is
   due. Lesson navigation returns to the review mission. `start_review` uses the
   existing exam lock; existing attempts still resume. Content authoring and source
   repair remain available. A failed review allows remediation before its next
   shorter retry becomes due. An abandoned attempt still counts as no success.
2. **Spacing ignored intervening study.** A review could claim delayed performance
   from an old pass even after recent explanations, practice or another exam on that
   capability. Spacing now starts at the last journaled learning/retrieval event for
   that capability before the review began; its record ID is exposed in evidence.
   Unrelated capabilities do not reset it. External study remains unobservable.
3. **Rounded hours could meet a threshold early.** Eligibility used the two-decimal
   display value. It now uses the unrounded elapsed interval. The 20-hour label
   threshold and review intervals remain engineering heuristics, not validated laws.

The original five audit issues are covered by the existing v7.1 tests: content-based
item identity, capability-version invalidation, fail-closed grounding, non-code
assessment, and the durable review queue. This follow-up strengthens the last one.

## Whole learner journey: mechanism versus evidence

| Stage | Implemented mechanism | Remaining limitation / next decision |
|---|---|---|
| Intake and goal | Goal/background contract, official target metadata, host-agent intake | Goal feasibility, realistic workload and competing constraints need a conversation; metadata is not a feasibility model. Ask what the learner needs to do, not what course they want to finish. |
| Diagnostic | Observed versus self-report entries; provisional gaps; generic probe/challenge machinery | Course diagnostics are host-authored observations, not a validated adaptive test. They do not reliably bind every diagnostic response to assistance/verifier provenance. Use one small performance task, then one discriminating prerequisite task only when needed. Never assign intelligence or a learning style. |
| Sources | Installed NotebookLM CLI, per-resource import/readability status, text hashes, exact quote verification, rights metadata | A matching quote does not make an interpretation true or resolve contradictory sources. Check date, authority, scope and disagreements separately. NotebookLM uses a remote service; this architecture is local orchestration, not offline NotebookLM. |
| Roadmap | Clickable HTML and React detail panels; prerequisites/outcomes; approval of the exact version; change impact | Sequencing, time estimates and coverage still depend on the host's curriculum judgment. Start with the smallest useful capability, preserve prerequisites, and expose provisional assumptions. |
| Teaching | Explanation, separate worked example, why, alternative explanation, optional self-explanation; support recorded | A polished lesson is not responsive tutoring by itself. The host must read the attempt, narrow confusing material and change the example. Lexical self-explanation coverage is not semantic grading. |
| Practice/production | Executable Python and concept/scenario practice; learner files/answers; hints and attempts recorded | Guided success is formative. Course executable exams remain Python-only; generic workspace checks support other toolchains. Voice is optional and is not independently proven in this audit. |
| Assessment | Frozen fresh items; content/behaviour duplicate detection; near-duplicate review; bounded non-code evidence; attributed rubric judgments | Novel IDs and wording do not establish transfer. Similarity thresholds need calibration on independently labelled pairs. Deterministic coding checks verify only authored conditions; recognition pass is weak evidence. |
| Feedback/advancement | Stored results, specific failed checks, misconceptions and next-mission reasons; a single rubric pass stays provisional | Two judgments by the same model are correlated, even on different items. Require calibrated human review before consequential decisions. Passing a node means the progression rule was met, not broad mastery. |
| Remediation | Targeted practice and alternative explanation after recurring failures | No robust automatic stop/escalation ladder. After repeated unsuccessful recovery, use a prerequisite/interpretation probe; discuss workload or tooling; reduce scope or ask a human tutor. Repeating the same explanation is not adaptation. |
| Retention | Durable due queue, shorter lapse retry, review-first runtime guard, fresh assessment, observed spacing | Simulated clocks show scheduling, not memory. Report unknown external re-exposure and missed reviews. Keep actual delayed outcomes separate from a due date. |
| Progress/accountability | Evidence exports, version history, active/idle separation, attempts, help and respectful blocker inquiry | Time is an exposure estimate, not effort or ability. The learner must be able to challenge a grade and inspect its criterion evidence. No automatic promise of deadline success. |
| Accessibility/privacy/recovery | Keyboard/dialog/navigation E2E, mobile layout checks, exam restart, journal recovery and private local caches | Screen-reader, cognitive/motor accessibility and optional voice need human testing. Local owners can inspect keys/checks or use outside help. Source caches/backups are sensitive; do not publish or upload them as test artifacts. |

## Practical host-agent protocol (proposed design, not a validated method)

1. Ask the concrete goal first, then one necessary background/constraint question.
   Offer accommodations and a text-only path. Check feasibility before promising a
   deadline. Keep the initial diagnostic brief; a five-minute budget is a starting
   usability hypothesis to evaluate, not an optimum.
2. Ask for a small explanation, decision or artifact with explicit acceptance
   criteria. If successful, use a different context before skipping a prerequisite.
   If unsuccessful, discriminate misunderstanding of instructions from a prerequisite
   gap. Record the actual response, help, confidence and why the estimate is uncertain.
   Never convert self-report or assisted practice into an independent score.
3. Research missing open resources; separate availability, authority, rights and
   unresolved coverage. For conflicting sources, explain which claim disagrees and
   its date/scope; do not hide the conflict in a generated summary. Ask before paid
   acquisition, file transmission, remote import or a new notebook write.
4. Show the roadmap, sources, dependencies and outcomes. Await actual learner
   approval before authoring/teaching. Scripted fixture approvals are demo actions.
5. Teach one idea, obtain learner production, then provide specific feedback. Change
   representation after confusion, fade help, and assess with a fresh unaided task.
   If two recovery cycles do not help, stop repeating the loop and investigate a
   prerequisite, task interpretation, accessibility or workload issue. This is a
   proposed escalation rule; monitor its burden and revise it from observations.
6. Resume from durable state each turn. Respect the exam lock and due-review guard.
   Explain what the current evidence supports and what remains unknown.

Prefer this small observed-task protocol and transparent skill-specific notes to a
large diagnostic questionnaire, an uncalibrated personality model, a hosted API
rewrite, or a more elaborate scheduler without comparative evidence.

## Primary evidence rechecked in this follow-up

- **China:** Huang et al. studied mathematical word problems in university samples;
  their experiments did not establish a general delayed retrieval advantage over
  worked-example restudy. This limits blanket claims that more quizzes necessarily
  improve procedural learning. [Primary full text](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2023.1093653/full).
- **Japan:** Kanayama and Kasahara's publisher abstract reports 53 learners and no
  long-term difference between expanding and equally spaced L2 vocabulary practice
  with four sessions. It does not justify this app's numeric intervals or transfer
  to adult technical problem solving. [Publisher abstract](https://www.jstage.jst.go.jp/article/arele/27/0/27_217/_article/-char/en).
- **Denmark:** Andersen, Guul and Humlum's field experiment combined extra reading
  instruction with information for teachers. Its result concerns that bundled school
  intervention; it cannot isolate an AI tutoring mechanism or prove this app works.
  [Primary working paper](https://humcap.uchicago.edu/RePEc/hka/wpaper/Anderson_Guul_Humlum_2019_instruction-time-information-achievement.pdf).

These studies motivate questions and boundaries. Country is context, not a ranking
of educational methods. No effect size or app parameter is inferred from them here.

## Evaluation before expanding automation

Keep `efficacy-study.md`'s equal-material/equal-time randomized comparison and external,
fresh delayed outcome. First run a consented feasibility pilot; do not claim efficacy
from it. Pre-register and report:

- diagnostic burden, completion and false skip/false remediation against subsequent
  fresh independent performance, stratified by prior knowledge and accommodations;
- time and attempts to the first independent success, plus transfer and actual
  delayed success, separated by assessment kind and assistance;
- remediation-loop frequency, recovery after changed explanation/prerequisite probe,
  learner frustration, inaccessible tasks, attrition and missing delayed outcomes;
- blind human criterion ratings on a held-out answer set, disagreement/adjudication,
  false passes and false failures for each AI/model/rubric version; check contradictions,
  irrelevant fluent answers, language variation and accommodations. Repeat AI ratings
  do not substitute for independent human agreement;
- item leakage and near-duplicate false accept/reject rates on independently labelled
  pairs, and source/interpretation errors even when quotes match;
- operational recovery, data loss, privacy incidents and out-of-app study disclosures.

Numerical skill estimates and AI grading should remain advisory until their errors
are measured on real held-out work. Expose uncertainty and a correction/appeal path.
Do not lower standards because a demo is smooth or a small pilot looks positive.

## Reproducible functional checks

Use a private scratch copy and a separate `ALEARN_STATE_DIR` for tests. Preserve the
learner's real trust store and sessions. Existing node_modules may be reused; the
following commands do not install software:

```sh
ALEARN_STATE_DIR=/tmp/alearn-audit-state python3 -m pytest -q
python3 skills/adaptive-learn/scripts/validate_package.py
python3 tests/e2e/course_ui_e2e.py --out /tmp/alearn-e2e \
  --node-modules /path/to/existing/node_modules
python3 tests/e2e/notebooklm_readonly_smoke.py \
  --notebook-id <explicitly-selected-existing-id> \
  --resources-file <existing-resource-manifest.json> \
  --lesson-file <existing-lesson-or-content.json> --out /tmp/alearn-live-read
```

The browser script exercises approval, teaching, assisted practice, Python and
non-code exams, rubric-pending lock, navigation/restart, stale grounding, capability
changes and reviews. Its labelled synthetic content is functional test data.
The separate NotebookLM script verifies existing imported resources and exact
readable grounding through the live CLI. It never creates notebooks, adds sources,
uploads files or sends model queries. It does not prove new-import behaviour or
learning efficacy. Keep its private source cache outside any shared artifact bundle.

On this Mac: baseline 558 tests + 20 subtests; follow-up 566 tests + 25 subtests.
Browser baseline 83 checks; follow-up 88 checks, 16 verified screenshots, retained
trace, no uncaught page errors. Vite production build and package validation pass.
Wheel build succeeds with bundled runtime/UI assets. Tested interpreter: macOS
Python 3.14; no claim that the other CI matrix jobs ran during this follow-up.

Live follow-up: NotebookLM CLI 0.7.3 read-only validation succeeded for all five existing resources and all nine lesson quotes. The record is `notebooklm-readonly/report.json` in the private audit workspace; no new import/upload or learner outcome was tested.
