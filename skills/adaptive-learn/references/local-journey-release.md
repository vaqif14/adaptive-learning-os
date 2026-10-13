# Local learner journey follow-up — 2026-10-13

The later CLI-first correction supersedes this release's initial shell-generation
description: see `cli-first-generation.md`. New CLI start is state-only; no personalized
HTML/workspace appears before clarified goal/diagnostic and readable sources. Optional
preapproval web intake requires `course serve --intake` and is labelled Tanışlıq.

This is a reversible local integration of the existing host-agent and CLI architecture.
NotebookLM remains the existing remote CLI service; this is not offline NotebookLM.
No push, deployment, new source transmission, notebook mutation or account change is
part of this release. The final audit evidence lives beside the private scratch checkout,
not in a public source cache. A passing workflow does not establish learning efficacy.

## What changed

- New CLI course sessions collect the goal before background, then capture at most two
  distinct scoped diagnostic responses. The host prepares each task and separately
  judges the captured response. Assistance survives renamed retries. Learners can
  explicitly skip an unsubmitted diagnostic with a reason; estimates remain provisional.
  Roadmap approval and lesson authoring wait for this intake. Existing sessions retain
  their explicit legacy policy; this is not a silent data migration.
- The app shows learner intake, host preparation/judgment waits, and a refresh action.
  Learner controls cannot assign their own grade. Each turn resumes from durable state.
- Same-agent free-text judgments do not become independent corroboration merely because
  there are two. Human attribution is self-declared, not an identity authentication
  system. AI-only delayed answers do not justify longer review intervals.
- Three consecutive independent failures pause the attempt cycle for a concrete changed
  approach and explicit learner agreement. The plan itself is not success evidence.
  Alternative explanations, prerequisite probes and human review remain host decisions.
- Invalid/missing source keys and stale prerequisite chains block current progression;
  historical evidence remains visible. Frozen submissions, item versions and rubric
  versions are checked before finalization. Malformed/empty grader output fails closed.
- Exam drafts follow attempt identity, old result bookmarks resolve to the correct
  committed result, and busy feedback prevents misleading enabled actions.
- A neutral production UI bundle accompanies the wheel; new workspaces use local assets
  without npm installation. It contains no learner or source material.

## Reproducible host-agent flow

Use the installed `alearn` console command, or the source script via
`python3 skills/adaptive-learn/scripts/alearn.py`. Choose a fresh workspace and a separate
`ALEARN_STATE_DIR`; do not reuse or overwrite someone else's learner session.

1. Run `alearn --workspace <root> start --topic <topic> --goal <goal>` and retain its
   returned session ID. Run `alearn --workspace <root> course init --session <id>`.
2. Start every host turn with `course turn --session <id>`. Submit `course intake
   --session <id> --goal <goal>`, then `--stated-background <answer>` as separate actions.
3. `course diagnostic-probe --session <id> --file <probe.json>` uses only
   `id`, `node_id`, `kind`, `prompt`, `criteria`. Kinds are explanation, scenario,
   artifact or retrieval. Use concrete prompts; never expose answer keys.
4. The learner submits `course diagnostic-answer --session <id> --response-file
   <answer.txt>` and any `--assistance` disclosure. Then the host runs
   `course diagnostic-judge --session <id> --file <judgment.json>` with only
   `outcome`, `assessor`, `confidence`, `rationale`. Retain actual provenance; do not
   label a model judgment human. Repeat with a different task. An accessibility or
   preference skip uses `course diagnostic-skip --reason <learner's concrete reason>`.
5. Verify target scope and owned/missing resources. Ask before paid acquisition or
   transmitting material. Validate readable source grounding through the existing
   NotebookLM CLI or explicit local cache evidence. Matching quotes do not settle
   conflicting interpretations. Propose a full dependency roadmap with outcomes.
6. Show the clickable HTML/app roadmap. Obtain actual learner approval of that version
   before lessons. Host-authored teaching includes explanation, worked example when
   useful, alternative explanation, practice, learner production and fresh assessment.
7. Run `course serve --session <id> --port <free-local-port>` and use its printed URL.
   Check the port first; never terminate another project's process. No network host or
   browser extension is required. Text is the supported baseline; optional voice has
   not been independently verified here.
8. Submit independent assessment, finalize any pending rubric judgment via the host,
   inspect evidence and follow remediation/review missions. Navigate away or restart
   during an exam to confirm the durable app lock. Due reviews come before teaching.

For synthetic functional demonstrations, run:

```sh
ALEARN_STATE_DIR=/tmp/alearn-tests python3 -m pytest -q
python3 tests/e2e/course_ui_e2e.py --node-modules <existing-node_modules> --out <new-output>
python3 tests/e2e/course_journey_e2e.py --node-modules <existing-node_modules> --out <new-output>
python3 skills/adaptive-learn/scripts/validate_package.py
```

Browser fixtures are explicitly synthetic and include controlled time movement.
They prove scheduling and restart behavior, not actual delayed human retention. The
separate read-only NotebookLM smoke proves existing imported source readability and
quote grounding; it does not prove new imports, browser source upload or learning gains.

## Four critical perspectives and limits

Learning science: scoped performance observations, assistance separation, fresh transfer
tasks and spaced independent reviews are design mechanisms. No fixed learning style,
global intelligence, laziness or incapacity is inferred from quiz answers or time. Skill
labels and scheduler thresholds are uncalibrated engineering judgments.

Product/UX: the host must adapt explanations from actual learner production, check
accessibility and feasibility, and explain why evidence counts. Polling does not create
a background autonomous tutor; host actions are still required. Screen-reader and real
voice testing remain outstanding.

Runtime/data integrity: state, response provenance, source hashes, roadmap versions,
attempts, activity intervals and next missions persist locally. Active/idle time is an
app observation; external study, disconnected time and owner-controlled clocks cannot
be securely established. User state is never a unit-test fixture.

Assessment/security: local exam access remains locked across app navigation/restart
until finalization or explicit abandonment. This is not cheatproof on an owner-controlled
computer. Python hidden checks verify authored conditions, not every real-world case.
Concept/scenario/rubric assessments work, but generic non-Python executable course exams
and calibrated automatic semantic grading remain limited.

These are review perspectives, not a claim of external expert consensus. See
`local-followup-audit.md` for bounded primary studies from China, Japan and Denmark and
`efficacy-study.md` for an equal-material/equal-time randomized evaluation protocol.
Before outcome claims: consented feasibility work, held-out blind human grading, false
pass/fail and duplicate-item calibration, and a preregistered fresh delayed external
assessment are required. Neither a smooth demo nor a small uncontrolled pilot supplies
that evidence.
