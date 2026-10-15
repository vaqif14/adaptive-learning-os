# Course Studio — background course production with a regenerated 10-rule gate

Status: design contract for v8.0 (2026-10-13). Every studio module, test and UI
screen implements this document; deviations are recorded in §14.

## 1. What the owner asked for

> Prepare the course in the background and hand the finished work over to us,
> like the reference platforms do; re-verify the 10 rules on every run; practice
> matters most — learn by watching an expert build, then doing it yourself.

Owner decisions (2026-10-13):

- Baseline v7.1 + intake/journey work committed as `b500d4b` before studio work.
- Background authoring may launch the locally installed `claude -p` and
  `codex exec` CLIs, **opt-in only** (`studio runners configure`), never by default.
- TutorFlow, Real Python, AI Hero, Mimo, DeepLearning.AI, Boot.dev and JavaRush are
  **pedagogy references only**. No content, transcript, video or branding is copied,
  and no walkthrough is attributed to a real expert. Walkthroughs are labelled
  *agent tərəfindən yazılmış, mənbəyə əsaslanan nümayiş* (agent-authored,
  source-grounded demonstration).

## 2. Adopted teaching patterns (from the references) and their guard rails

| Pattern (reference) | Adopted as | Rule guard |
|---------------------|-----------|------------|
| Watch an expert code (Real Python, DeepLearning.AI) | **Walkthrough**: stepped code-along on a *different instance* than practice/exam; every step's code runs in the sandbox and must print its declared checkpoint output; one deliberate mistake + its fix; predict-then-reveal prompts | Watching earns **zero** evidence (R1); walkthrough code must FAIL the practice and exam hidden checks (R8 answer-leak probe); quotes grounded or labelled supplementary (R7) |
| Problem → solution, exercise repo (AI Hero), local checker (Boot.dev CLI, JavaRush IDE plugin) | **Faded ladder** (worked → completion with blanks → independent) + **exercise** files in the learner workspace, checked by the runtime (`practice-check`) | Ladder/practice evidence is `supported_completion`, never mastery (R1/R5); solution + hidden checks vault-only (R8) |
| Many small graded tasks (JavaRush, Mimo) | **Studio targets**: per node ≥ 3 fresh exam items (core + transfer coverage), 3 ladder rungs, 1 practice task, 2 review seeds | Freshness by content identity (fresh-v1); retakes of the same fingerprint never count (R3) |
| Async cohort + office hours (AI Hero) | Not adopted (needs humans/schedule); due-review queue + mission reasons cover pacing | — |
| AI tutor that hints, not answers (Boot.dev, TutorFlow) | Existing hint ladder + per-lesson **hint ceiling**; **reveal** is explicit, costs all credit for that task and forces a new attempt (same task, no credit) | R8 |
| Async job + idempotency + webhooks (TutorFlow Content API) | Durable job + stage keys + body-bound idempotency; **pull** notifications (no webhooks, no daemon) | R10 local-first |
| XP, streaks, certificates (Boot.dev, Mimo, JavaRush) | **Rejected** as progress or proof: exposure must not look like mastery | R1/R3/R9 |
| Hosted AI generation, video avatars (TutorFlow) | **Rejected**: the runtime never calls a hosted model; content is host-agent authored and validator-admitted; no synthetic "expert" video | R7/R9/R10 |

## 3. Actors and handover

- **Owner** (the platform team, "biz"): commissions a build for a course session,
  reviews the **handover dossier**, accepts or rejects per node, accepts the build.
- **Host agent** (Claude Code / Codex; current session or opt-in headless runner):
  the only author. Its output is untrusted data, admitted only through validators.
- **Runtime**: deterministic orchestrator and referee. No network client, no LLM.
- **Learner**: unchanged CLI-first journey. Approves the exact roadmap version
  (R6). Staged studio content is never visible to the learner before promotion.

Promotion (staging → live course) requires, all re-checked at promotion time:
1. the course's approved roadmap version equals the build's `roadmap_version`;
2. a **fresh** passing rules receipt bound to the build digest and the ledger head;
3. an owner acceptance event bound to the same build digest;
4. every unit re-validated against the live course (grounding, freshness,
   separation, capability version) through the normal `install_lesson` /
   `install_items` admission path.

If the roadmap is not yet approved, the accepted build waits (`awaiting_learner_approval`);
the learner's approval of that exact version triggers promotion on the next `course turn`.
No second learner gate is added.

## 4. Storage (per course session, never deleted)

```
<session>/studio/
  jobs/<job_id>.json            job document (atomic write, 0600)
  events.jsonl                  hash-chained studio events (prev_hash, seq); head anchored in the ledger
  orders/<job_id>/<order_id>.json   immutable work orders
  inbox/<job_id>/<order_id>/    runner scratch result drop (result.json only is read)
  staging/<job_id>/<unit_id>/<digest>.json   validated, non-servable payloads (0600)
  receipts/<job_id>/<receipt_id>.json        signed rules receipts
  handover/<job_id>/HANDOVER.md|json         dossier per build digest
  scratch/<run_id>/             rules-check scratch sessions (0700), rotated by rename, never removed
  archive/                      superseded jobs/orders/scratch
  notifications.jsonl           pull notifications (handover ready, blocked, needs owner)
```
Directories 0700, files 0600. Code in `studio_*.py` and `rules_check.py` must not call
`unlink`/`rmtree`/`os.remove` (grep gate test); superseding = rename into `archive/`.

## 5. Job model and state machine

Job statuses: `planning → authoring → validating → checking → remediation →
ready_for_review → accepted → awaiting_learner_approval → promoted`, plus terminal or
parked: `blocked` (rules or system defect, human needed), `needs_learner`
(intake/roadmap prerequisites missing — never fabricated), `superseded`
(drift), `cancelled`. Transitions are table-driven and fail closed.

A job binds `{session_id, roadmap_version, generation_basis, capability versions,
source hashes, policy versions}`. Any drift (roadmap, capability, source hash,
validator/policy version) moves it to `superseded`; `studio rebase` creates a new
job reusing units whose input hashes are unchanged.

Units: per node `lesson` (incl. walkthrough + ladder), `items`. Each unit has
`inputs_hash`, `attempts`, `status` (`pending → ordered → submitted → valid |
invalid → staged`), `last_errors`, `staged_digest`.

Stage key = sha256(job_id, stage, inputs_hash, policy versions). Completing a stage
key twice is a no-op. Every mutation takes the session lock briefly, reads the job
revision and writes atomically; body-bound idempotency keys refuse reuse with a
different body.

Budgets per job: `max_attempts_per_unit` (3), `max_remediation_rounds` (3),
`max_runner_seconds` (900 per order), `max_output_bytes` (1 MB per result).
Leases: an order taken by a runner has `lease_until`; expired leases are reclaimed.

`build_digest` = sha256 over the sorted list of `(unit_id, staged_digest)`, the
roadmap version, source hashes and validator/policy versions (Merkle-style root).

## 6. Work orders (what the author gets)

Need-to-know, ids-only plus data files:
- `order.json`: order id, unit, node spec (title, task, outcomes, skills, practice,
  requires, exam_domain), item-kind guidance, targets, contract name, previous
  errors (on retry), language, label requirements, forbidden content list.
- `sources/<source_id>.txt`: cached **validated** source text, only for resources
  whose expected kind is a public `url`; private files/uploads are excluded unless
  the owner runs `studio runners configure --allow-private-sources` (recorded).
  Source text is untrusted data; the order says so.
- Lesson orders never contain exam items; item orders never contain the lesson's
  practice solution.
The author writes **only** `result.json` (lesson payload or items payload).

## 7. Practice-first lesson parts (validated, optional for legacy lessons, required by studio targets)

`walkthrough`: `{title, scenario_md, language, steps: [{explain_md, code, checkpoint
{expected_output}, predict? {prompt, reveal}}], mistake: {step, wrong_code,
symptom, fix_explain}, transcript_md, refs, label}`. Validation: every step's
cumulative code runs in the sandbox (python first; other languages via execution
backends, fail closed if unavailable) and prints exactly `expected_output`; the
mistake's wrong code fails or prints a different output; final code must FAIL the
practice checks and every exam item's checks; ≥ 3 steps; no named real expert.

`ladder`: three rungs `worked` (full solution shown, explained), `completion` (code
with `___` blanks + blank answers, checked by running), `independent` (= the lesson
practice). Rung solutions must differ from exam items (separation check).

`hint_ceiling`: max hints shown before the ladder offers only reveal.
`reveal`: explicit learner confirmation; ledger support event; practice credit 0;
forces a new practice attempt (same task; no credit, evidence stays assisted).

## 8. Regenerated 10-rule gate (`alearn rules-check`)

Recomputed from scratch on **every** run: after each validation round, each
remediation round, at handover, at owner acceptance, before and after promotion, at
export, and (quick scope) inside `course turn` when the last receipt is stale. It
never reads a previous receipt or any agent claim.

- Dynamic probes run on a scratch copy (`backup-session` → `restore-session` →
  trust adoption) with the staged build promoted **into the scratch copy**; the real
  session must be byte-identical afterwards.
- Every probe has an id, a version, and a **negative control** (a deliberately faulty
  fixture that must make it fail). If a control passes, the suite is broken.
- Exit codes: 0 all pass; 2 a rule failed; 3 probe error (never counted as a pass).
- Receipt: `{receipt_id, build_digest, ledger_seq, ledger_head (before == after),
  code_tree_hash, probe_set_hash, results[rule, probes, pass], created_at}` signed
  with HMAC using the ledger trust key; digest anchored as a ledger event.
- Hard rules R1, R4, R6, R7, R8, R10 are non-waivable. A failing build still gets a
  signed dossier marked BLOCKED with the failing probes.
- A receipt means "these invariants hold under these probes" — never "quality proven".

Per-rule checks: Appendix A (deterministic check, adversarial probe, block condition).

## 9. Remediation loop

Findings are classified: `content_defect` (author can fix: grounding miss, overlap,
failing self-test, missing coverage, forbidden claim) → a fix order with the finding
text, max 3 rounds per unit; `system_defect` (ledger, trust, sandbox missing, code
invariant) → `blocked`, human action, never auto-fixed; `learner_gated` (intake,
approval, private source authorization) → `needs_learner` with the exact next step.

## 10. Execution modes

1. **Inline (default)**: the current host-agent session loops
   `studio next-task` → author → `studio submit` until `idle|needs_learner|done`.
2. **Detached worker**: `studio start --detach` spawns `alearn studio worker` in its own session behind a flock
   singleton (`<session>/studio/worker/worker.lock`, heartbeat `heartbeat.json`, log `worker.log`). Every tick performs the
   next runtime-owned step: plan, the full rules gate and handover, submit a result already in an order inbox, or (only with
   an enabled runner) claim an order, run the runner and submit its output. It exits on done, `needs_learner`, `blocked`,
   `cancelled` or SIGTERM, and sleeps with backoff (30 s up to 300 s) while paused, idle (exam active) or waiting for the
   inline host until an idle limit (2 h). `studio status` reports `worker {alive, pid, beat_at, state}`;
   `studio tick` is one iteration for cron/launchd (skipped while a worker loop holds the lock);
   `studio schedule-print` PRINTS a launchd plist and a crontab line and installs nothing.
3. **Runners (opt-in)**: `claude -p` / `codex exec`, configured per session with `studio runners configure --confirm`
   (never on by default). A runner runs in a fresh `alearn-run-*` scratch directory outside the workspace holding only copies of
   `order.json` and `sources/*.txt`, with an allowlist-built environment (no `ALEARN_*`, tokens, cookies, base URLs, agent
   sockets), its own process group, a timeout and an output cap. claude gets NO tools; codex a per-run permission profile
   that reads only the run directory and minimal platform paths and writes nothing (see §14 for what that does and does not
   guarantee). The runtime, not the runner, writes the extracted answer to the order's
   `out/result.json` and calls `submit`, so admission is identical to the inline host. Provenance per run (runner, binary
   sha256, version, exit code, timeout/stop flags, duration, stdout bytes, prompt and output sha256; the model label and
   cost the runner reports are labelled untrusted) lives in the job's order entry and in an anchored `runner_finished` event;
   raw stdout/stderr stay in the run directory only.

## 11. Interfaces

CLI: `alearn studio init|plan|status|next-task|order-show|submit|check|events|pause|resume|cancel|rebase|handover|accept|reject|promote`,
stage D: `studio runners configure|show|disable`, `studio start [--detach]`, `studio tick`, `studio schedule-print`,
`studio doctor`, `studio notifications [--since N]` (and the hidden `studio worker` the detached start launches),
`alearn rules-check --session <sid> [--job <id>] [--scope quick|full]`,
`alearn course validate-lesson|validate-items --node <id> --file <payload>` (dry run).

HTTP (owner surface, stage E): `alearn studio serve --session S [--host 127.0.0.1] [--port 8778]` runs `runtime/server.py` in
owner mode, a process and credential SEPARATE from `course serve`. It requires `ALEARN_OWNER_TOKEN` (environment only, >= 24
characters, different from `ADAPTIVE_API_TOKEN`), never allows anonymous access, refuses a wildcard bind, and applies the learner
server's Host/Origin, JSON-only POST, body cap and security-header rules to every studio request (a foreign Host or Origin is 403
even with the token). Routes: `GET /api/studio` (job summary, units per node, worker heartbeat, runners without secrets,
notifications), `GET /api/studio/events[?limit=]`, `GET /api/studio/handover` (HANDOVER.json + Markdown of the current build
digest; read-only, never recomputes the gate), `GET /api/studio/node?node=` (the public learner view of the staged node plus
COUNTS of hidden checks); `POST /api/studio/{start,pause,resume,cancel,accept,reject}`. `accept` takes exactly `{digest, statement}`
and applies the CLI's checks; `reject` takes `{node, note}`; `start` launches the detached worker. No route accepts authored
content (other fields are a 400). The learner course server never mounts `/api/studio*` (404 before any credential check), and
learner routes never expose staging.

UI (one bundle, two servers; `/health` names the mode and only the owner code path loads the owner screens):
- Owner: token form (memory + sessionStorage, Bearer header only), Studio (status, per-node unit progress, worker heartbeat,
  runners, notifications, recent events, start/pause/resume/cancel), Handover (build digest, BLOCKED banner, R1-R10 table with plain
  Azerbaijani labels and per-rule probe counts in words, NOT-verified and absent lists, per-node preview, accept with the digest
  shown and a typed statement, reject per node with a note).
- Learner: Walkthrough stepper (label banner, explanation, code, predict-then-reveal, expected checkpoint output, the deliberate
  mistake highlighted with symptom and fix), faded ladder (worked, completion with visible `___`, independent = the practice),
  hint-ceiling message, reveal and reset behind confirmation dialogs (reveal states the cost; reset archives, never deletes). All
  through the existing `/api/course/help` (`walkthrough`, `ladder` + `rung`) and `/api/course/practice/reveal|reset` routes.

## 12. Migrations

Course state schema 3 (additive): `studio` block `{active_job, promoted_builds[]}`,
`promotion_history[]`, lesson parts allowed. `down_v3_to_v2` archives v3-only keys
(like v2→v1) and is export-only. Studio store has its own `studio_schema` (1).

## 13. Verification per step

Each build step ships tests; the whole suite, validator, wheel smoke and E2E stay
green; a fake-agent fixture (well-behaved and hostile) drives the studio end to end
in CI without any network; mutation checks prove each rules probe flips when its
enforcement is weakened.

## 14. Deviations log

Stage A deviations (recorded after the three-lens review, 2026-10-13):

- Unit status path is `pending -> ordered -> submitted -> staged | invalid`; the `valid` step is not persisted
  because validation and staging happen in one locked operation (a unit is never `valid` but unstaged).
- Extra job statuses (`paused`, `needs_learner`, `superseded`, `blocked`, ...) and per-unit orders with leases exist
  beyond the section 5 sketch; the job table in `studio_jobs.TRANSITIONS` is authoritative and tested.
- A rebased job reuses an `items` unit only when its lesson was reused too (separation is re-checked pairwise).
- The items target also requires `min_core_items` (>= 1) `independent_reproduction` items besides transfer coverage.
- `max_remediation_rounds` counts re-validation failures (`job.remediation_rounds`); exceeding it blocks the job with
  a `content_defect` (`remediation_rounds_exhausted`).
- Staged payloads must contain only the validated top-level fields; unknown fields are a counted rejection.
- JSON nesting deeper than 32 levels is rejected as `malformed_json` (a counted attempt).
- Events are appended before the job document is saved, and every event batch is anchored in the ledger, so a crash
  never leaves a staged unit without its audit trail and a dropped tail is detected. A torn (unterminated) final
  event line is moved to `studio/archive/events-torn-N.jsonl` and the log is cut back to its last full line.
- Resuming a paused job pushes issued leases out by the time spent paused.
- Stage A has no separate owner channel: `studio init --allow-private-sources` is the only switch and a later `init`
  with a different policy is refused (`studio_policy_mismatch`). The owner-only `studio runners configure` route is
  stage B work; until then the flag is an owner decision taken on the CLI, not an agent-safe control.

Stage B deviations and decisions (practice-first teaching, 2026-10-13):

- Policy `studio-targets-v2` is the default for new jobs and requires a walkthrough for every lesson (python
  practice lessons: language `python`; concept lessons: language `none` allowed), plus a faded ladder and a
  `hint_ceiling` for python practice lessons. `studio-targets-v1` stays valid: a job keeps its own policy id, its
  binding is compared against that id (no drift from the version bump), and rebase keeps it. v2 targets cannot be loosened.
- A checkpoint's `expected_output` is the whole cumulative stdout of steps 1..i (the harness prints nothing extra);
  comparison ignores trailing whitespace per line and at the end. A python walkthrough needs `mistake.wrong_code`
  and the mistake step must carry a checkpoint to compare against.
- Answer-leak guard: "fail" means the final walkthrough code (and the ladder's filled completion and worked code)
  does not satisfy ALL checks of the practice and of every python exam item. It runs both when a lesson arrives
  (against the items already committed or staged) and when items arrive (against the lesson's walkthrough/ladder).
- The ladder's worked rung must not itself solve the practice (tightening of the spec); the completion rung with
  filled blanks must pass the practice checks and with empty blanks must fail them.
- Walkthrough refs join the live grounding gate (`course_content.lesson_ref_parts`): a changed or unreadable source
  closes the walkthrough, ladder, hints, practice and reveal like any other lesson part.
- Person-name guard: the walkthrough has a closed key set (person-like keys such as expert/author/instructor are
  rejected with their own message) and a heuristic scan of its prose for a role word followed by a capitalised name.
  The scan is best effort; the closed key set is the hard rule.
- `practice_reveal` marks the attempt `revealed` and refuses `practice_check`/`practice_answer` for it
  (`practice_revealed`) until `begin_practice` issues a new attempt id (the old attempt moves to
  `practice_history`). The mission-level support provenance keeps `ai_direct_answer_revealed`, so the new attempt's
  evidence is assisted and carries the revealed flag too. `practice_reset` archives to
  `workspace/modules/<node>/archive/<utc>-<name>` and never deletes.
- The learner UI bundle is not changed in stage B; the walkthrough/ladder/reveal/reset surfaces are the CLI and the
  HTTP routes `/api/course/practice/reveal`, `/api/course/practice/reset` and `/api/course/help` (kinds
  `walkthrough` and `ladder`, `rung`).

Stage C deviations and decisions (rules gate, remediation, handover, promotion, 2026-10-13):

- Files: `runtime/rules_check.py` (engine, receipts), `runtime/rules_probes/r01..r10.py` (45 probes, each with an id, a version,
  `deterministic|dynamic` and a negative-control builder), `rules_probes/journey.py` (the scripted scratch journey),
  `studio_receipt.py` (signing, freshness, promotion conditions), `studio_remediate.py`, `studio_handover.py`,
  `studio_review.py` (mixin of `StudioKernel`: gate routing, handover, accept, reject, promote, turn hooks) and
  `rules_loopback.py`. The loopback harness is the ONLY module of the gate that speaks HTTP (`http.client` against the
  server it starts on `127.0.0.1:<ephemeral>`); it is outside the gated set (`studio*.py`, `rules_check.py`,
  `rules_probes/*.py`), which therefore contain no network client import and no deletion call (AST + text gate tests).
- Scopes: `quick` runs the probes marked `cost=cheap` (pure reads of the session files, the ledger and the staged build;
  about 0.1 s) and reports every other probe as `skipped` (never a pass). `full` also runs the `sandbox` probes
  (answer-leak guard, sandbox availability) and creates the scratch copy for the `scratch` probes. A selection of probes
  (`probe_ids`/`rules`) or a replaced registry yields a `partial` receipt that can never satisfy acceptance or promotion.
- Negative controls run first, from synthetic faulty contexts (`Ctx.synthetic`) or with a broken enforcement function
  injected through `ctx.hook`; a control that does not fail, is not applicable or crashes makes the receipt `suite_broken`
  (exit 3) and nothing counts as a pass. Probes look enforcement functions up at call time, so a weakened implementation
  (counts_as_strong, FORMAT_MAX_STRENGTH, find_quote, public_item, the loopback guard, ledger verification) flips the
  matching probe (mutation tests).
- Scratch copy: `backup_session(..., exclude=("studio/scratch",))` (new keyword; the output may live inside the session
  directory only under an excluded prefix) -> `restore_session` (which adopts trust) into
  `<session>/studio/scratch/<run_id>/ws`, with its own `ALEARN_STATE_DIR` (`<run_id>/state`) swapped in around the dynamic
  phase after the ledger key is cached. The scratch directory is locked down to 0700/0600 afterwards. A scratch promotion
  needs an approved roadmap: when the real learner has not approved yet, the scratch copy (only) is approved with the
  statement "rules-check rehearsal ..." and the build is promoted with `rehearsal=True` (owner acceptance and receipt are
  waived for the scratch copy only; no CLI or `StudioKernel` path passes that flag). The real session is byte-identical
  afterwards (tests compare every file outside `studio/scratch` and the real trust state directory). The scratch backup
  leaves `studio/scratch` and `studio/archive` out (`backup_session` now excludes `studio/scratch` by default: the copies are
  disposable duplicates), and only the five newest scratch runs stay in `studio/scratch`; older ones are renamed into
  `studio/archive/` (`rotate_scratch`), never removed.
- Dynamic probes judge observations recorded by one scripted journey (`journey.run`): HTTP probes (staged paths and
  traversal variants, `/api/studio*`, course routes, Host/Origin/content-type guards), binding attacks on promotion (changed
  staged byte, source hash, capability version, roadmap version, no acceptance, acceptance for another digest, agent-channel
  approval), the rehearsal promotion (zero evidence and attempts added), source attacks (every cached copy of a source
  corrupted, a one-character quote change, a claim without a ref), planted canaries and the real reference solutions
  searched in every learner payload, hint ceiling and confirmed reveal, the exam lock (course actions, `attest`,
  `verify-python` and every `EXAM_LOCKED_COMMANDS` entry), per-node correct exams (python, concept, scenario; free_text is
  abandoned because it needs an external grader) and retake exhaustion. A step that crashes records its error and the probes
  that needed it report a probe error (exit 3). Vault items are hash-bound to their exams, so canaries are planted in items
  only after the last exam.
- Not applicable (reported with a reason, never a silent pass): the studio HTTP routes and the runner environment scrub
  (stage D has no studio routes and no runners yet; the guard probes enumerate `/api/studio*` routes in `server.py` and fail
  any that answers a foreign Host), the live `stale_check` of NotebookLM sources (needs the CLI; the gate uses the manifest's
  `stale` flag), declared-vs-derived counts and caveats before a handover exists, and every build probe in a session without
  a studio job.
- R1(c) is implemented as: a passed node needs a ledger evidence record that is correct, unassisted, runtime-verified
  (`course_exam_verifier`, `python_verifier`, `exercise_verifier`, `workspace_check`) and checked (or a free_text rubric
  judgment); `counts_as_strong` is replayed on every record and a strong record must additionally come from such a source.
  A concept/scenario pass is weak/medium by design (R2) and is not required to be strong. R3's "advance with weak-only
  evidence" and R2's "build report declares the same maximum" are covered by the tier derivation (`_exam_evidence` is
  executed for every kind), the table hash recorded in the receipt (`evidence_table_hash`) and the derived-count check of
  the handover; there is no separate advance probe.
- Receipt: `{receipt_id, scope, partial, job_id, build_digest, roadmap_version, course_revision, ledger_seq_before/after,
  ledger_head_before/after, code_tree_hash, probe_set_hash, evidence_table_hash, results[], waivable, non_waivable, fix_only,
  failing_rules, errored_rules, not_applicable[], controls, status, exit_code, passed, claim, scratch, created_at, signature}`.
  `waivable` lists R2, R3 and R5 as information only: acceptance refuses ANY failing rule; R9 is fix-only (never overridden).
  `commit_receipt` stores it under `studio/receipts/<job>/` and anchors its digest as a ledger `event` (`rules_receipt`,
  source `studio`). A receipt is FRESH while only studio events (and the note `course_promotion_checked`) were appended to
  the ledger after `ledger_seq_after`; learner evidence or a course state commit makes it stale. Receipts sort by ledger
  position, not by timestamp. The gate itself never reads a stored receipt; the accept/promotion paths verify the signature,
  digest, anchor and freshness of the stored receipt they act on, and R6 checks the receipt an acceptance cites from its
  anchored ledger event only.
- Gate wiring: when a job reaches `checking` the kernel runs the full gate (`StudioKernel(gate=True)`, the default; the stage
  A/B fixtures pass `gate=False`). Pass: handover written, a quick receipt re-lints the handover itself (R9), then
  `ready_for_review` and a `handover_ready` notification. Failure: `studio_remediate.classify` -> content defects re-open the
  named units with the finding text (`rules_check:R9:...`, per-unit `rules_rounds` against `max_remediation_rounds`, then
  `blocked` with `remediation_rounds_exhausted`); system defects (and an unknown probe, a probe error, a broken suite) block
  the job for a human and are never handed to the author; learner-gated findings park the job in `needs_learner` with the
  exact next step and it resumes when a quick recheck passes. A failing build still gets a signed BLOCKED dossier.
- Handover: the current dossier is `studio/handover/<job>/HANDOVER.{md,json}` and a copy bound to its build digest is
  `studio/handover/<job>/<digest>/HANDOVER.{md,json}`; a superseded dossier (new receipt, new digest after remediation, a
  BLOCKED one replaced by a passing one) is renamed into an `archive/` folder, never overwritten or removed. Counts
  are derived from the staged artifacts (`derive_nodes`) and re-derived by R9 on every later run. Plain Azerbaijani labels for
  R1-R10, the absent list and the not-verified list; the wording needs native review before reuse outside the owner dossier.
- Owner actions: `studio handover` (recomputes a missing or stale full receipt while the job is `ready_for_review`),
  `studio accept --job --digest --statement` (requires `ready_for_review`, the exact digest, a signed anchored FRESH passing
  FULL receipt for it; appends a ledger `event` `studio_owner_acceptance` with provenance source `studio_owner`; the owner
  channel is `owner_cli` - there is still no separate agent-proof channel, see the stage A note), `studio reject --job --node
  --note` (ledger `studio_owner_rejection`, re-opens the node's lesson and items with `owner_note: ...` as their fix order).
- Promotion: `CourseKernel.promote_build` checks, under the session lock and from the ledger/live course: course guard +
  approved roadmap version == the build's == the installed roadmap's, the unit set, the digest recomputed from the unit
  payloads (`studio_jobs.build_digest_of`), live source hashes and capability versions == the build's, then (real promotion
  only) the owner acceptance and the signed anchored fresh passing full receipt, then validates EVERY node (lesson against
  the new items, items against the new lesson, freshness and targets via `evaluate_unit`) before writing anything; only then
  are all nodes installed through the same `_apply_lesson`/`_apply_items` as `install_lesson`/`install_items`, in ONE state
  commit (so a failure in any unit installs nothing, stronger than "atomic per node"). Re-promoting the same verified build is
  a no-op. `StudioKernel.promote` (also `alearn course promote-staged`) recomputes a stale receipt first (`refresh`), refuses a
  failing one, then runs the full gate again on the promoted course and records it in `promotion_history`
  (`course_promotion_checked`). If the roadmap is not approved the accepted job waits in `awaiting_learner_approval`;
  `course turn` attempts promotion once the learner approved that exact version. No second learner gate exists.
- Course turn: `turn` carries `rules_receipt` (`status`, `scope`, `failing_rules`, `source` stored|quick_recomputed,
  `stale_receipt`) and `studio_promotion` when a promotion was attempted. A stale stored receipt is replaced by a quick
  recomputation that is not stored. When a non-waivable rule fails (or a probe errors) for a PROMOTED build, `blocks_teaching`
  is true, `teaching_allowed` is false and the agent directive names the failing rules; individual learner mutations are not
  blocked by it. `UI_SCHEMA_VERSION` stays 2: the bridge contract (`/api/course/*` view) is unchanged.
- Course state schema 3 (`studio {active_job, promoted_builds[]}`, `promotion_history[]`): pure idempotent `_v2_to_v3`,
  `down_v3_to_v2` archives under `v3_archive` (export only; `course schema-down --target-version 2|1`), the exact pre-v3 state
  is journaled once as `course-state.pre-v3.json`. Older migration steps no longer add the v3 keys. Existing tests that read
  schema 2 or chained `down_v2_to_v1` were updated for schema 3 only.
- Privacy fix found by R10: `mkdir(parents=True, mode=0o700)` applies the mode to the leaf only, which left `assessment-vault/`
  and `course/` world-listable (0755). `utils.private_dirs` (used by `write_json`, `write_bytes` and `write_private`) now
  creates every missing parent with 0700. Sessions created by an older release keep their modes and make R10 fail until the
  owner runs `chmod 700` on those folders (a system defect: the studio does not touch the real session outside `studio/`).

### Stage C review fixes (gate soundness, promotion, handover)

- **A crashed journey step is an error, never "nothing to check".** Any entry in the scratch journey's `errors` (a step that
  raised, including `StopIteration`) makes every dynamic probe report `error` with the step names (exit 3); a crash that
  escapes `journey.run` is caught the same way. The journey records an explicit `retake.skipped` reason when no node could be
  passed instead of leaving the retake check silently absent.
- **R9 negation is bound.** A negator cancels a claim only right before it (one word: "not proven", "no guarantee") or at most
  two words after it in the same clause ("sübut olunmuş optimal interval deyil"); punctuation or other words break the
  binding. The negative control is now the five bypass sentences (negator somewhere else in the sentence). Cost: a claim
  followed by a two-word gap and "deyil" is read as negated (a deliberate false negative; Azerbaijani wording needs native
  review).
- **R10 static scan.** Forbidden: subprocess, asyncio, socketserver, imaplib/poplib/nntplib, ctypes, webbrowser,
  multiprocessing, selectors, `os.system/popen/exec*/spawn*`, `shutil.move`, bare `exec/eval/compile/__import__`,
  `import_module`, `getattr` with a computed or deletion/process attribute name. The gated set is no longer only a filename
  glob: every runtime module reachable from it through relative imports is scanned for NETWORK CLIENT imports (`dep:` rows;
  `urllib.parse` and the sandbox's `subprocess` are legitimate there). `server.py` and `rules_loopback.py` are the documented
  exempt harness and are not entered. Intentional limit: a string-built `__import__`-free route through a non-literal
  `getattr` is flagged, but a deletion reached through a third-party library call is out of scope of a static scan.
- **R8 exam lock reads the real wiring.** The probe parses `scripts/alearn.py` (its `EXAM_LOCKED_COMMANDS` set and the
  `assert_no_active_exam` dispatch, plus the `sources-ask` branch) and `studio.py` (`plan`, `next_task`, `submit` consult
  `exam_locked`), and checks the studio's own `studio_snapshot()['exam_locked']` during a real exam. Removing a command or a
  guard fails R8. It does not drive `alearn.main()` end to end (each command needs its own arguments); it reads the dispatch
  source instead, which is what a removal would change.
- **Receipts are bound to the current enforcement.** `studio_receipt.enforcement_problems` compares `code_tree_hash`,
  `probe_set_hash`, `probes_run` coverage, per-rule verification, `job_id`, `roadmap_version` and `session_id` with the
  gate as it stands now. `StudioKernel._latest_full` (accept, handover, promote) treats a mismatch as stale (the gate is
  re-run) and `require_promotion_conditions` refuses it. Any edit to a `runtime/*.py` file therefore invalidates earlier
  receipts: intended.
- **`incomplete` status.** A full, non-partial receipt in which a rule is `skipped`, or a hard rule has no passing probe, has
  status `incomplete` (exit 3, `unverified_rules`), classified as a system defect; promotion needs `pass`. Not applicable
  remains allowed for non-hard rules and for individual hard-rule probes whose subject does not exist (e.g. no python
  practice), as long as another probe of that rule passed.
- **Promotion adds only the build's items to the exam pool.** Items live for a node but absent from the build are retired
  at promotion: removed from `item_versions` (so `_items` and `_item_status` never offer them), the binding is marked
  `retired` (reason `not_in_promoted_build`), vault files and bindings are kept, and the promotion record lists
  `retired_items`. Refusing promotion instead was rejected: shown items must be republished under new ids, so a refusal
  would block every rebuild.
- **Promotion is atomic for files too.** `promote_build` journals before-images of every file the install loop may write and,
  if the transaction does not commit, restores overwritten files and PARKS newly created ones under `course/rollback/<id>/`
  (renamed, never deleted). If the state had committed (recovery completes it) nothing is rolled back. A hard crash (kill)
  between file writes and the state commit is not covered by this in-process journal; it would need the files in the
  ledger-journaled `commit_state` set (not supported for vault/lesson files today).
- **Course turn guards the promoted build, not the newest job.** `turn_receipt` evaluates blocking against the most recent
  promoted job whatever the newest job is (`live_job_id`, `live_failing_rules` are reported).
- **Owner acceptance stays an unauthenticated CLI statement (intentional).** The handover now lists
  `owner_acceptance_unauthenticated` under NOT_VERIFIED and the acceptance records whether the CLI had a terminal
  (`interactive`). Auto-promotion on such an acceptance is NOT blocked and no secret or interactive confirmation is required:
  an agent in the same account can read any secret the owner can, and a terminal check is trivially reproduced, so a gate
  that looked authenticated would be false assurance. A real owner channel (out of band, stage D UI) is the remedy.
- **Owner withdrawal.** `studio reject` is also allowed from `accepted` and `awaiting_learner_approval` (new transitions to
  `remediation`); the rejection event revokes the acceptance (`find_acceptance`).
- **Gate runs on big or odd sessions.** The scratch copy skips symlinks (counted in `obs.restore.skipped_symlinks`) instead of
  failing; a real `backup-session` still refuses them and now names the path, and the archive-limit error names the file and
  counts. Rotated scratch runs move to `studio/archive/scratch-runs/` (not `studio/archive/`); that folder and
  `studio/scratch` are excluded from backups and from the scratch copy, and the R10 mode audit checks a rotated run as one
  directory instead of walking it. Rotated copies are still never deleted (they hold learner data; the owner removes them).
- **Session lock during the full gate: kept.** The full run holds the session lock for its whole duration (about 5-10 s) and
  `promote` runs the full gate up to twice under it. Releasing the lock for the journey was rejected: the receipt asserts "the
  ledger did not move during the run" and a lock-free journey would turn every concurrent learner action into a spurious R4
  failure (livelock for an active learner instead of latency). The cost is latency for other processes during promotion;
  stage D runs the gate in the worker without a bounded wait (see Stage F: latency accepted).
- **`ALEARN_STATE_DIR` swap is refused next to other threads.** The scratch run mutates a process-global variable, so it now
  raises a probe error when any other thread is alive and serialises concurrent gates; a threaded host must run the gate in
  its own process (as `alearn rules-check` does).

### Stage D deviations and decisions (opt-in runners, detached worker, 2026-10-14)

- Files: `runtime/studio_agents.py` (runner configuration mixin of `StudioKernel`, environment builder, prompt, argv builders,
  process-group runner, extraction, `dispatch`) and `runtime/studio_worker.py` (singleton, heartbeat, `Worker.tick/run_loop`,
  detach, `schedule_print`, `doctor`, notifications). They are the ONLY studio modules that import `subprocess`, `signal` or
  `fcntl`; processes are started only through `subprocess.Popen(<argv>, shell=False)`. The static gate test
  (`test_v800_studio_static_gates.py`) and the R10 probe `r10.no_network_or_deletion` enforce exactly this (the probe's
  `PROCESS_EXEMPT` set, with a negative control: `shell=True`, `subprocess.run`, `os.system` still fail). Network clients,
  deletion calls and dynamic code stay banned in both modules.
- Kernel additions: `claim_order` (refuses a non-issued order, a job that does not accept work, an active exam, or ANOTHER holder's
  live lease; sets `taken_by`/`lease_until`; event `order_claimed`), `runner_finish` (records provenance and settles the lease:
  `completed` / `failed` / `stopped` / `skipped`), `finish_checking` (runtime-owned gate run for a build parked in `checking`),
  `job_budgets`, `runners_*`, and `status.worker`. The runner runs WITHOUT the session lock; every kernel call locks briefly, and
  `submit` re-checks drift (an order superseded during a run is refused and the stale result is never written).
- Runner failures (spawn error, timeout, non-zero exit, empty output, `output_too_large`, an envelope with `is_error`, an
  unparseable envelope) never reach `submit`: the order is `released`, the next `next_task` re-issues it, and the unit counts a
  `runner_failures` streak. Three in a row block the job as a `system_defect` `runner_unreliable` with a notification; a
  completed run resets the streak. A runner failure is not a content attempt (`attempts` is untouched), a SIGTERM stop releases
  the lease without counting anything, and an answer that is prose, invalid or carries injected fields is a NORMAL attempt (it goes
  through `submit`, so remediation, the unknown-field check and the attempt budget apply unchanged).
- The worker never issues orders unless a runner is enabled (an unanswered lease would burn an attempt on expiry). Without a
  runner it plans, runs the gate/handover, submits an inbox result that the inline host placed, and otherwise reports
  `waiting`. A runner binary whose sha256 changed since configuration is not executed: the runner becomes `needs_reconfigure`
  (event + notification) and stays unusable until `runners configure` is run again; the hash is checked before an order is
  leased, in `dispatch`, and once more right before the exec (a hash-to-exec gap of microseconds remains; the binary is not
  exec'd by file descriptor). `studio doctor` never executes a binary whose hash drifted.
- Configuration (`runners.json`, 0600, schema 1): `timeout_seconds` is an integer in `MIN_TIMEOUT (60)..max_runner_seconds-30` so a
  runner always times out before its lease; `pass_env` is validated against a per-runner allowlist (claude: `ANTHROPIC_API_KEY`;
  codex: `OPENAI_API_KEY`, `CODEX_HOME`) on write AND on every load (a hand-edited file fails closed); only names are stored, never
  values. A binary is refused when its realpath is inside the workspace or session directory, is not a regular executable file, or
  is group/world-writable (an addition to the spec), or when `--version` does not name `Claude Code` / `codex-cli`. The version probe
  itself runs under the scrubbed environment in a scratch directory. `--allow-private-sources` remains a `studio init` policy flag
  (there is no separate runner-level switch); `runners show` and the configure event record whether the live job allows it.
- Flags actually used (checked against `claude 2.1.267 --help` and `codex-cli 0.155.0 exec --help`, never run beyond
  `--help/--version`): claude `-p --output-format json --tools "" --strict-mcp-config --mcp-config '{"mcpServers":{}}'
  --disable-slash-commands --no-session-persistence --safe-mode --settings '{"disableAllHooks":true}' [--model] [--max-budget-usd]`;
  `--safe-mode` switches off CLAUDE.md, skills, plugins, hooks and MCP while keeping login. `--setting-sources` was deliberately NOT
  used: dropping user settings could remove an owner's `apiKeyHelper` and make the runner fail for a reason the studio cannot see.
  codex `exec -c default_permissions="alearn_runner" -c permissions.alearn_runner.filesystem={":minimal"="read","<run_dir>"="read"}
  --disable browser_use|browser_use_external|browser_use_full_cdp_access|computer_use|in_app_browser|apps|plugins|image_generation|hooks
  -C <run_dir> --skip-git-repo-check --ephemeral --ignore-user-config --ignore-rules --color never -o <run_dir>/last_message.txt [-m] -`
  (no legacy `-s`: it would override the profile; see the post-review hardening entry below). The claude envelope is read as one JSON object (`result` string, `is_error`, `subtype`,
  `total_cost_usd`, `model`/`modelUsage` keys): none of it is trusted. The envelope shape is not verified against a live call
  (no model call was made); a different shape fails closed as `envelope_unparseable`/`envelope_no_result`.
- Honest limits of the runner sandbox. codex's sandboxed tools can read only the run directory and the minimal platform paths of
  its permission profile (enforcement is codex's own Seatbelt profile; checked for codex-cli 0.155, re-check after upgrades);
  `unified_exec` cannot be switched off in 0.155, so codex keeps a shell inside that profile. claude has no tools at all, so it cannot read or write files. Neither runner is
  network-isolated: the vendor CLI talks to its hosted model by design (that is what `--confirm` acknowledges). A grandchild that
  leaves the process group with `setsid` is not reached by the group kill; a runner can fill the disk of its own scratch directory
  (only stdout/stderr are size-watched, stdout at the output cap, stderr at 8x). The run directory is never deleted by the studio
  (the owner removes `alearn-run-*` folders); its path is in the provenance. The runner's raw output is never copied into events,
  the job or logs.
- The output cap applies to the runner's raw stdout (claude: the JSON envelope, which escapes non-ASCII text, so a payload close
  to the cap may exceed it as an envelope; real payloads are a few tens of KB) and to the extracted text again inside
  `parse_strict`. Over the cap the process group is killed as soon as the next poll (50 ms) sees it and at most `cap + 1` bytes are
  ever read. An order whose prompt exceeds 512 KB is not dispatched: `runner_skipped:order_too_large`, the order is handed to
  the inline host (`taken_by: inline`, a fresh lease) and the worker waits.
- Worker: `fcntl.flock(LOCK_EX|LOCK_NB)` singleton held for the process lifetime; the heartbeat is rewritten atomically on every tick
  and while sleeping/running (no thread is used because the rules gate refuses to run next to other threads). `alive` means the lock
  is held AND the heartbeat is at most 120 s old (a held lock with a stale beat reports `stale`). SIGTERM/SIGINT set a flag that
  the runner poll loop sees within 50 ms: the runner's group is killed, the lease released (`worker_stopping`) and the worker
  exits cleanly. The worker commands (`start`, `worker`, `tick`) are exempt from the CLI's outer session lock (a runner executes for
  minutes); every kernel call locks per operation. A detached worker log past 5 MB is renamed into `studio/archive/worker-logs/`.
- `studio schedule-print` prints text (not JSON) so the plist can be pasted. It embeds `ALEARN_STATE_DIR` only when the printing
  process has it set (a path to the ledger trust heads, not a secret) and never any other environment value.
- R10 `runner_env_scrubbed` is no longer not-applicable: it feeds a hostile environment to the REAL `studio_agents.scrubbed_env` and
  fails on anything outside the allowlist; its negative control still uses an injected faulty builder.
- Tests (`tests/test_v804_runners_worker.py`) never start the real CLIs: fake runner scripts in a temp dir outside the workspace answer
  `--version` and behave by mode. A detached worker is a separate process, which caches its own ledger key, so those tests pass the
  parent's key through the worker's environment (`ALEARN_LEDGER_KEY`); the runner's scrubbed environment is asserted never to carry it.
- Azerbaijani: only the prompt asks the runner for Azerbaijani learner text; all owner-facing messages of this stage (CLI JSON,
  notifications) are English. The prompt's wording is not learner-visible.

### Stage E deviations and decisions (owner HTTP surface and UI, 2026-10-14)

- Files: `runtime/studio_views.py` (read projections; joins the gated modules: no network import, no deletion, no process
  machinery), owner mode in `runtime/server.py`, `studio serve` in `scripts/alearn.py`, owner/learner screens under
  `templates/react/src` (`Root.jsx` picks the mode from `/health`; the owner and learner code are separate lazy chunks), and the
  rebuilt `templates/react/dist` (built from `src` with the repo's default vite setup and the lockfile's packages; the E2E's
  `--check-dist` asserts the committed dist equals a fresh build).
- The owner server is a separate process and credential. `ServerConfig(owner_session=, owner_token=)` fails closed: no token, a
  token under 24 characters, a token equal to the learner/API token, `allow_anon`, a wildcard host, or `course_session` at the same
  time are `ValueError`s; the owner token never falls back to `ADAPTIVE_API_TOKEN`. `studio serve` has no token argument.
- Owner mode serves `/health`, the built UI and `/api/studio*` only; every learner, run, inspect and roadmap route is 404 there. Any
  other server (learner, plain `serve`) answers `/api/studio*` with 404 before authentication. Host/Origin are checked before the token.
- The gate never runs inside the HTTP server (it refuses to run next to other threads). Therefore `GET /api/studio/handover` is
  read-only (`handover(refresh=False)`): a stale or missing receipt is reported (`receipt_fresh`, `stale_reason`) and the owner
  refreshes it with `alearn studio handover` / `rules-check`. `accept` over HTTP calls `accept(channel="owner_ui", promote=False)`:
  the acceptance is recorded (event `studio_owner_acceptance`, channel `owner_ui`) and the job stays `accepted`; promotion, whose
  post-promotion gate swaps process-wide state, is the owner's separate step `alearn studio promote` or the learner's next
  `course turn` (both in their own process). The response says so (`promotion.state: deferred`, with the command).
- `accept`/`reject` gained a `channel` argument limited to `owner_cli | owner_ui` (the receipt module already listed both);
  `handover_view` text for the not-verified acceptance item now names the owner token as a second unauthenticated channel.
- `start` spawns the worker through `studio_worker.start_detached` (new optional `env`): the child environment is the server's minus
  `ALEARN_OWNER_TOKEN` and `ADAPTIVE_API_TOKEN`. A worker that finds nothing to do exits with code 0 before the spawn handshake;
  the UI reports that as a complete run, not a failure. The log path is not echoed to the browser.
- The node preview is built from the validated staged payload with the same `course_content.public_*` functions the learner routes
  use. Practice shows prompt, starter code and the visible check names; the ladder's completion rung shows the template and the blank
  COUNT, never the blank answers; items are `public_item`. A final scan compares every string of the preview, whitespace-collapsed,
  against the vault (reference solutions, hidden check and detector source, exemplars >= 15 characters) and refuses the preview
  (`studio_preview_leak`, 500) on any hit. Short answer keys (a single option id) cannot be scanned for and are excluded by shape instead.
- HANDOVER wording: per-rule counts are words (`2 keçdi · 1 tətbiq olunmur (səbəb)`, failures as `KEÇMƏDİ`, errors, quick-scope skips),
  in `HANDOVER.json` (`probe_summary_az`, `probes_not_applicable`) and in `HANDOVER.md`; the old `passed/total` fraction no longer
  appears (it counted a not-applicable probe as a failure). Dossiers written before this change lack the new keys; the UI falls back
  to `N keçdi`.
- R10 `loopback_guard` was extended and one expectation CHANGED: studio routes used to be probed for 401 on a foreign Host; the learner
  server must now answer 404 (not mounted), and the owner server is probed through a second loopback server: 200 with the token, 401
  without or with a wrong token, 403 for a foreign Host or Origin, 415 for a non-JSON POST, 404 for learner routes, and the three
  `ServerConfig` refusals. The probe's off-loopback case now passes `token=""` so the result no longer depends on `ADAPTIVE_API_TOKEN`
  being unset in the environment (a pre-existing environment dependency found by the browser E2E).
- Learner UI: the confirmation dialog moved to `components/ConfirmDialog.jsx` (exam texts are its defaults) and takes a title, text, labels
  and `safeFirst` (focus starts on the harmless choice, used for reveal and reset). The hint ceiling comes from `lesson.practice_first.hint_ceiling`
  and `progress.help.hints`; the revealed state from `progress.practice.revealed`. After a reveal, check/answer are locked
  (`aria-disabled`) until "Yeni cəhd başla" calls `practice/begin`. Predict-then-reveal text is never stored or sent.
- Azerbaijani UI wording is plain text written without a native review; terms flagged in code comments for review: "nümayiş",
  "proqnoz", "təhvil", "qəbz", "quruluş", "pilləli təlim".
- Not covered: the owner UI has no screen for runner configuration, `init`, `rebase` or `promote` (terminal only, by design: the
  runner configuration records what leaves the machine); the owner token is a bearer secret with no rotation or expiry; the browser
  E2E runs the gate in subprocesses only for promotion.
- Tests: `tests/test_v805_studio_http.py` (config, credentials, rebinding/CORS, shapes, preview confidentiality and its leak guard,
  accept bound to the digest and refused when stale, reject, controls, the serve command), the extended static gate test, and the browser
  E2E `tests/e2e/studio_e2e.py` (CI job `browser-e2e`).

### Stage E review fixes (accessibility, wording, config)

- Walkthrough: a mistake step that also has a prediction keeps its symptom and fix closed until the prediction is revealed (same gate as
  the answer and checkpoint). Next/Previous focus the step title and scroll it into view; arrow keys move focus to the new stepper
  button and only Left/Right are taken (Up/Down scroll). Next/Previous use `aria-disabled` (focus is never lost at either end); the
  live region is the step title only. The mistake step carries a visible `⚠` in the stepper plus a legend line.
- Ladder: opened rungs get `aria-expanded`/`aria-controls`, an "açıldı" mark and focus on their heading; an open rung is never
  refetched (each fetch is a help event). DEVIATION: rung 3 and rung 2 stay always available (the learner owns the pace); rung 2 only
  shows a "1-ci pillə tövsiyə olunur" hint until rung 1 was opened, it is not locked.
- Practice: reveal/reset tools use `aria-disabled`; after a reveal focus moves to the result heading. The reset case in the finding
  does not occur (reset is offered only while not revealed), the same `aria-disabled` pattern is applied for consistency. The
  concept form's `Yoxla` is `aria-disabled` and its handler returns early once revealed, with a visible "Yeni cəhd başla" hint.
- Owner UI: the cancel dialog makes header, footer and the Studio body inert. Wording changes: "Tokeni sil və çıx", "yığma" for
  build ("Yığma işi", "yığma izi", also in `HANDOVER.md`), "Nələr yoxdur" / "Nələr yoxlanılmayıb" (also in the Markdown dossier),
  "qəsdən edilmiş səhv", "pillə". All need native review. DEVIATION: not-applicable reasons (free-form probe text, mostly English) are
  NOT translated: a mapping would go stale with every new probe; instead a cut reason ends with `…` and the table carries a legend
  that "tətbiq olunmur" is not a pass. Raw event/notification type codes in the panel stay as written (they are audit identifiers).
- Old dossiers: `handover(refresh=False)` fills a missing `probe_summary_az` from the counts the old file kept
  (`with_probe_summaries`, a copy; the file is not rewritten), so the UI no longer falls back to a bare "N keçdi".
- `ServerConfig` owner mode refuses a token with leading/trailing whitespace (the presented value is compared trimmed, so such a
  secret could never authenticate).
- Tests: `tests/test_v805_studio_http.py` (padded token, ellipsis, legacy summary, old dossier over HTTP) and `tests/e2e/studio_e2e.py`
  (gated mistake, focus after Next and arrows, inert cancel dialog, ladder aria and no refetch, reveal focus, legend).

### Stage F fixes (integration, red-team and rule audit findings, 2026-10-14)

Each finding was confirmed by reading the code path or reproducing it in a temp session; regressions are in
`tests/test_v806_stage_f.py` (plus tightened assertions in `test_v802_handover_promotion.py`, `test_v805_stage_d_review.py`,
`test_v805_studio_http.py`).

Fixed:

- **Worker heartbeat thread made every background gate fail (critical).** `Worker._busy` no longer starts a `studio-beat`
  thread; the scratch run refuses to swap `ALEARN_STATE_DIR` next to other threads. `rules_check.run_rules(progress=...)` calls
  a zero-argument callback before each probe and `StudioKernel.gate_progress` carries the worker's rate-limited beat. The
  full gate now reaches `ready_for_review` under `studio start|tick`, the hidden `studio worker` and the owner UI Start.
- **Full gate during an active exam blocked the job as a system defect (high).** `finish_checking` raises `exam_active`
  (the worker idles with `exam_active`), `_after_checking` leaves the build in `checking`, the `handover` refresh skips the gate
  with a stale reason, and `_promote_txn` returns `deferred_exam_active` before it would run the gate. Nothing moves to `blocked`.
  DEVIATION (not done): a `blocked -> checking` re-check transition. `blocked` stays human-only; its probe-error cause during
  an exam is closed at the source, and any other system defect is meant to be inspected (`rebase` or `cancel`).
- **Restore left intermediate directories 0755 (high).** `restore_session` uses `utils.private_dirs` (0700 on every created
  parent). Related: `submit_exam` created `course/attempts/<id>/submitted` with the process umask; it now uses `write_bytes`
  (0700 parents, 0600 file). Both tests run under umask 022 and assert `r10.mode_problems == []`.
- **Engagement heartbeats staled the rules receipt (medium).** `engagement_interval` is a note event in `freshness()`.
  DEVIATION: a roadmap approval still stales the receipt (it supersedes capability progress, so it is evidence-relevant), and
  `accept` still refuses a stale receipt instead of recomputing it: the owner accepts the dossier they were shown, and the
  refusal names `alearn studio handover`. The owner UI cannot refresh (the gate cannot run in a threaded server).
- **Promote during an exam stranded an accepted job (medium).** See the exam item above; the job stays `accepted`.
- **Old session frontend after promotion (medium).** DEVIATION: `UI_SCHEMA_VERSION` stays 2 (a bump would invalidate every
  built session and all clients for a cosmetic gap). Instead `CourseKernel.frontend_status` compares the session frontend source
  with the shipped template and a promotion reports `frontend: {outdated, files, next_step}` naming `course regenerate-ui` and
  `course build-ui`.
- **Reveal 'new variant' (low).** The wording was wrong, not the evidence rule: a new attempt is the same task with a new
  attempt id and no credit. UI text, section 2/7 and `course-runtime.md` now say so, and `begin_practice` resets the per-attempt
  hint counter after a reveal (previously the ceiling shut the new attempt out of hints). A real variant needs an alternate
  practice instance in the lesson schema and is not built.
- **Owner accept 'deferred' (low).** `accept(promote=False)` now answers `awaiting_learner_approval` (job stays `accepted`)
  when the bound roadmap version is not approved. DEVIATION: a UI-only learner still needs `alearn studio promote` (or any
  `course turn`) to trigger promotion; the threaded server cannot run the post-promotion gate.
- **R3/R5/R8 restructured copies (medium/high).** The lexical alpha-rename guard is complemented by a behavioural probe:
  the snippet's top-level functions are bound, by arity, to the names the target's hidden checks call and the checks run
  in the sandbox (`course_content._alias_variants/_behaves_like`). It now runs on walkthrough and ladder snippets, on fenced
  code and code-like strings in explanation, why, worked example, alternative example, practice prompt and hints (against the
  practice and every exam item) and between an exam item and the practice (each side's reference against the other's checks).
  Residual: functions with different arity, classes, or logic split differently from the reference are not matched, and
  `scope` labels remain author-declared. Both are listed in the handover `not_verified` list (`behavioural_equivalence_bounded`).
- **R9 lexicon (medium).** Text is NFKC-normalised, format characters (zero-width, bidi) dropped and Cyrillic/Greek look-alikes
  folded before linting; added guarantee stems, assured, certain, make-you-an-expert, everyone-passes, numeric efficacy
  claims, and Azerbaijani derived forms (`ustalaş*`, `mənimsə*`, `təminat*`, `ekspert edəcək`, `hər kəs mütləq`). Still a lexical
  blocklist: native-speaker review remains open (`claim_lint_lexical` in `not_verified`).
- **R10 sandbox (critical).** Confirmed: the AST policy let a generator-frame walk (`gi_frame`, `f_back`, `f_globals`) and module
  internals (`re.enum.sys`) reach real modules, and `str.format` templates walk attributes at run time. Changes: frame and
  introspection attributes, `format_map`, `.format` on anything but a literal template without attribute/index fields, and
  imported-module internals are refused; the harness is a function (no module globals) and installs an audit hook that refuses
  network, process, deletion/rename, file-write and out-of-stdlib file-read/listing events while learner code and trusted checks
  run (denials are `PermissionError`; the hook is switched off by a closure cell for the harness's own result write). The hook
  was verified by running the harness WITHOUT the AST filter and walking to `io.open`/`os.listdir` (all refused, plus a
  negative control). These in-process layers are defence in depth, not a boundary.
- **Post-F hardening (2026-10-14): OS boundary for verifier children.** `runtime/os_sandbox.py` wraps every
  `PythonVerifier` child (studio admission, the rules gate, learner checks) in the platform sandbox when one works:
  macOS `sandbox-exec` (Seatbelt: network denied, exec only inside the Python installation, writes only in the run dir,
  reads of /Users, /Volumes, the temp roots, HOME and the state dir denied with the interpreter and run dir re-allowed),
  Linux `bwrap` (read-only root, unshared net/pid/ipc, tmpfs over home/temp/state roots, only the run dir writable).
  Detection runs a real wrapped child once per process. Opt-in runners DISPATCH ONLY when it works
  (`os_sandbox_unavailable` otherwise), because runner output is code that admission and the gate execute. New probe
  `r10.os_sandbox_contains` (45 probes now) proves containment with a canary file, an outside write, a local connect and a
  shell exec, plus a negative control that must read the canary unwrapped; without an OS sandbox it is not_applicable for
  host-authored builds and FAILS for runner-authored ones. `studio doctor` reports `os_sandbox`. Verified on macOS 26
  (Seatbelt): every action denied wrapped, allowed unwrapped; a profile mutant without the read deny is caught.
  Residual: the boundary is only as strong as Seatbelt/bubblewrap themselves; Linux without bwrap (e.g. a stock CI image)
  has no OS boundary, so runners stay disabled there and host/learner code runs under the in-process layers only (the
  handover's `sandbox_not_a_boundary` item says so). The fork syscall stays allowed inside the boundary (a framework
  Python re-spawns itself); children inherit the sandbox.
- **Owner statements (low, by design).** The statement stays free text and is not run through the R9 corpus, but a secret-like
  pattern or home path (`r10.SECRET_PATTERNS` minus e-mail) in an acceptance statement or rejection note is refused
  (`studio_statement_sensitive`, HTTP 400) because the ledger is append-only. Runner confirmation statements are not checked.

Not changed:

- **Bounded wait for the gate (low).** The session lock is still a blocking `flock` for the whole gate (5-10 s, up to ~20 s in
  `promote`). The stage C sentence 'should run the gate in a worker with a bounded wait' is amended: the worker runs the gate
  but the latency is accepted (a timed lock would turn learner requests into spurious 'busy' answers and the receipt requires a
  ledger that did not move). Revisit only with a measured complaint.
- **R6 human authority.** Approval and acceptance channels remain convention; `owner_acceptance_unauthenticated` is disclosed.

## Appendix A. Per-rule check specification

Derived from the judged studio design (four AI design lenses + judge, 2026-10-13); the
implementation may tighten a check, never loosen it.

### R1 anti-fake-mastery

- **Deterministic:** Replay projection.counts_as_strong over every ledger evidence record of the course. Assert: (a) every strong record has a runtime-verifier source with correctness_checked true and independence unassisted; (b) zero practice or walkthrough records with independence unassisted or strength above supported_completion; (c) every node_progress passed has an evidence_id satisfying counts_as_strong or the documented two-signal free_text corroboration; (d) evidence and attempt records authored by studio/agent runners == 0 (build added nothing); (e) fabricated fixtures (self-reported, AI-assessed, hinted, revealed, attested) all return False from counts_as_strong; (f) `attest` with a disallowed assessor exits non-zero.
- **Adversarial probe:** On the scratch copy: inject a self-declared 'mastered' claim, a strong record without a verifier source, a revealed-solution attempt, and a hint-assisted correct answer; each must be rejected or capped. Negative control: the fixture with a studio-written strong record must make the probe fail. Mutation test: weakening counts_as_strong must flip this probe.
- **Blocks handover if:** Any strong record lacks a verifier source or correctness_checked; any studio-authored evidence exists; any fabricated fixture counts as strong; attest accepts a disallowed assessor; the probe returns unverifiable. Non-waivable.

### R2 evidence tiers

- **Deterministic:** Compute the maximum strength mintable per item kind (python strong, concept/scenario medium, mcq-only weak, free_text medium and provisional, walkthrough/ladder none or supported_completion) and compare it to FORMAT_MAX_STRENGTH, evidence_semantics table hash and the build report. Assert no learner payload carries a client-supplied strength or format. Finalize one synthetic correct answer per kind on the scratch copy and assert the result strength <= the table.
- **Adversarial probe:** Submit a payload with strength='strong' and format='free_recall' injected; it must be ignored. Negative control: an item kind declared stronger than the table must fail the probe.
- **Blocks handover if:** Any kind can mint more than the table, the report declares a different maximum, a payload field overrides strength/format, or the table hash is not recorded in the receipt.

### R3 mastery gating

- **Deterministic:** Roadmap DAG uses `requires` ids that all exist, with no `prerequisites` key; start_mission/start_exam on a locked node refused; a capability change supersedes passes and locks dependents; permitted_actions never offers start_exam for a locked node; repeated retakes of the same fingerprint cannot reach mastery (fresh-v1: at least 3 fresh items per node).
- **Adversarial probe:** On the scratch copy: try to start the exam on a locked node, advance with a weak-only evidence set, and retake an identical item; all must be refused. Negative control: a DAG with an unknown requires id must fail.
- **Blocks handover if:** Any node lacks the fresh-item minimum, the DAG is invalid, a locked node can be started, or advance is possible without verified evidence.

### R4 ledger integrity

- **Deterministic:** `ledger-verify --force` returns keyed ok before and after the run; the pre-run prefix hash at the pre-run seq is unchanged (append-only); studio event chain verified and its head anchored in the ledger as a studio_event; backup then restore into scratch then adopt_trust succeeds; the receipt signature verifies against the ledger trust key.
- **Adversarial probe:** On a ledger copy: flip a byte, truncate the tail, keyless re-chain, downgrade to unkeyed; each must fail verification (exit 2). Negative control: the tampered ledger must be rejected, else the probe suite is declared broken.
- **Blocks handover if:** Any verification is not keyed ok, the prefix changed, a tamper is accepted, the event anchor is missing, or the signature does not verify. Non-waivable; classed as system_defect (blocked_system, human action, never auto-fixed).

### R5 transfer scopes

- **Deterministic:** For each node the build report shows core plus unseen transfer present and independent_reproduction; near/far transfer on non-trivial nodes; no supported_completion record is mastery_eligible; the delayed claim needs a ledger gap of at least 20h; scope coverage is derived from staged items, not declared.
- **Adversarial probe:** A review completed 1 hour after the pass must not count as delayed; a ladder completion must not count as transfer. Negative control: a node with core-only items must fail the coverage probe.
- **Blocks handover if:** A node lacks required scope coverage, practice evidence is mastery_eligible, or delayed is awardable without the gap.

### R6 roadmap authority

- **Deterministic:** approved_roadmap_version == roadmap_version == staged roadmap_version == a version recomputed from roadmap.json; the last approval_history entry matches version, channel (ui/cli, never agent) and statement; the approved build_digest == the recomputed bundle digest and receipt_digest matches; generation_provenance equals the generation basis; the roadmap source authority is recorded; staged ids are unreachable from every learner route (HTTP probe against the loopback test server returns 404/409).
- **Adversarial probe:** Mutate one staged byte after approval, change a source hash, bump the capability version, or submit an agent-channel approval; each must void or refuse. Negative control: approval bound to a different digest must fail promotion.
- **Blocks handover if:** Any version or digest mismatch, a missing human-channel approval, staged content servable, or a promotion attempted without the bound approval. Non-waivable.

### R7 source epistemics

- **Deterministic:** Re-verify every lesson/walkthrough ref with find_quote against the bound content_sha256 of the cached object; manifest complete and every used resource validated, not stale (stale_check re-run now), identity ok; rights gate allowed and upload_authorized_by_learner never set by the studio; supplementary-claim share reported and thresholded; every expert_source resolves to a validated resource.
- **Adversarial probe:** Corrupt one cached object in the scratch copy: grounding must fail closed (grounding_invalid); alter a quote by one character; plant a claim with no ref. Negative control: an ungrounded lesson must fail the probe.
- **Blocks handover if:** Any quote fails to re-verify, any source is stale/unvalidated/unauthorized, the supplementary share exceeds the threshold, or an expert attribution lacks a resolvable validated source. Non-waivable.

### R8 cognitive-delegation gate

- **Deterministic:** Public payload diff for lessons, walkthroughs, exercises, hints and items against the vault: no check code, key, reference solution, rubric exemplar or distractor map; the walkthrough sample FAILS the practice hidden checks; the exam lock refuses help/lesson/sources-ask/set-roadmap/studio submit/reveal (423 or refusal); evaluate_delegation(replace, independence_required) is blocked; the hint ceiling is enforced and any hint gives independence != unassisted; planted canary tokens never appear in learner payloads.
- **Adversarial probe:** Request the solution via hint farming past the ceiling, via reveal without confirmation, and via a crafted question during the exam lock; ask for a delegated 'do it for me' on a protected cognition. Negative control: a payload containing a reference solution must fail the probe.
- **Blocks handover if:** Any leak, any lock bypass, a demo equal to or answering the practice, a hint ladder that reaches the full solution, or delegation allowed. Non-waivable.

### R9 honest measurement

- **Deterministic:** Lint every learner-visible and export string (handover, UI copy, README, transcripts) for forbidden claims: 'proven', 'guaranteed', 'will master', unqualified 'effective', 'expert-quality' without a source, 'mastered' without strong evidence, unbacked percentages; efficacy caveat keys and the explicit 'not verified' list are present in the handover; every manifest count equals the count derived from validated artifacts; mastery % comes only from the projection; efficacy_meta is metadata only; a passing receipt reads 'invariants hold under these probes', never 'quality proven'.
- **Adversarial probe:** Plant 'guaranteed mastery' in a lesson, a declared count that differs from the derived count, and a missing caveat; each must fail. Negative control: copy with an efficacy claim must fail.
- **Blocks handover if:** Any forbidden claim, any declared-vs-derived count mismatch, a missing unverified list or efficacy caveat, or a receipt that claims quality. Waivable only by fixing content, never by reviewer override.

### R10 privacy and security

- **Deterministic:** File-mode audit (0600 files, 0700 dirs) over vault, staging, studio, scratch, sources/objects and logs; static no-network scan of studio_*.py and rules_check.py (AST: socket/urllib/http.client/requests/ssl) with server.py excluded; secret/PII/home-path regex scan over staging, report, handover and worker logs; loopback guard probe (Host/Origin, JSON content-type, token off-loopback) on every studio route; sandbox availability, failing closed when absent; subprocess-runner env scrub assertion (no ALEARN_STATE_DIR, tokens or cookies); no route accepts authored content.
- **Adversarial probe:** Make a 0644 vault file, plant a fake API key in staged text, send a foreign Host/Origin to a studio route, run the runner fake agent that tries to read env secrets, write outside scratch and open a socket; all must be caught. Negative control: the fixture with a world-readable vault file must fail.
- **Blocks handover if:** Any mode violation, secret match, network import, route guard gap, missing sandbox for required checks (no unsandboxed degrade), or an env leak into runners. Non-waivable; a missing backend is a needs_learner/blocked_system with instructions.

Stage B review fixes (2026-10-13), superseding the matching bullets above:

- Answer-leak guard, behaviour: it now runs against EVERY visible state of the teaching code, not only the final one:
  each cumulative step prefix, each step's own code, the mistake's `wrong_code` alone and spliced into the
  cumulative code, every ladder worked step (string, each list item and the joined list) and the completion
  template and its filled form (the filled completion is expected to solve the practice, so it is only checked
  against exam items). The same set is re-run when items arrive later.
- Answer-leak guard, structure: passing the checks is not the only test, because a renamed copy of a reference
  solution fails checks that call the original names. Every snippet and the walkthrough/ladder prose are also
  compared with the practice and exam-item reference solutions by an alpha-renamed token fingerprint (identifiers
  that are not keywords/builtins renamed by first appearance in the window; comments, docstrings and `;` ignored;
  numbers and strings keep their value). Deliberate deviation from "literal-insensitive": a different instance of
  the same shape (other constants) must stay valid, and a constant change would also stop the code solving the
  task, so it is not a leak of the solution. References shorter than 12 tokens must equal a whole snippet.
  Not covered: restructured solutions (other operators or control flow), and concept practices have no code
  reference, so list-form ladder text is compared with python exam items only.
- The guard is tri-state: a sandbox timeout or missing verdict (after one retry) raises `sandbox_inconclusive`
  and the lesson/items are rejected, so a flaky run can never read as "does not solve".
- Person-name guard additionally rejects two capitalised words followed by an attribution verb or postposition
  ("Andrew Ng deyir ki", "... tərəfindən") and two capitalised words after a lower-case word mid-sentence, except
  an allow-list of technical terms and ALL-CAPS tokens. Still best effort and may reject a rare legitimate pair of
  capitalised words; the author rewords. Native review of the verb list is advisable.
- `practice_reveal` is re-deliverable: a confirmed reveal on an already revealed attempt returns the solution again
  (no second observation, no new event, no second credit spend), and an idempotent replay re-derives the solution
  from the lesson instead of returning the stripped memory. The observation is guarded by the attempt id so a retry
  after a failed commit never records a second reveal. This replaces the earlier "one reveal per attempt refusal".
- Reveal is sticky per node: `_record_practice` forces `assisted` and `ai_direct_answer_revealed` when any attempt of
  the node (current or `practice_history`) was revealed, so switching nodes (which resets the mission-level
  provenance) cannot launder the reveal. It is not scoped by capability version.
- `practice_reset` cannot join the journaled state commit (workspace files are not in the transactional state-file
  set), so retries are made harmless instead: nothing is archived or rewritten when the file already equals the
  starter. A failed commit may leave the starter restored without a reset event; the learner's work is in the archive.

Stage D review fixes (2026-10-14):

- `runners.json` is not trusted alone: `enabled_runner` requires the verified event chain to show a `runner_configured`
  (same binary and sha256) as the last owner action for that runner, and re-runs `check_binary` (location, writability) at
  dispatch. A hand-written or re-enabled entry yields `runner_not_attested` / `runner_binary_unsafe`.
- Orphan runner groups: the pgid and process start time of the runner in flight are recorded in `worker/runner.json`; the next
  worker that takes the lock kills a group it finds there (a reused pid is recognised by its start time) and archives the record
  under `archive/worker-runs/`. `run_group` kills the group in a `finally`, and a failing heartbeat write is non-fatal.
- A job cancelled/paused or an order taken over mid-run makes `runner_finish` fail with `studio_order_not_held`; every outcome now
  maps that to a `stale` result and the tick reports `waiting`, so the next tick sees `cancelled`.
- Gate/handover run under a background heartbeat; a second SIGTERM/SIGINT restores the default action. `start --detach` waits up
  to 2 s for the child to take the lock and reports `already_running`/`exited_early` for the loser of a race.
- `schedule-print` prints a crontab line only for cadences cron can express evenly (divisors of 60, whole hours dividing 24);
  otherwise a comment points to the launchd plist.
- Post-review hardening (2026-10-14), codex HOME exposure CLOSED: the legacy `-s read-only` mode (which could read every file the
  account can read, e.g. `~/.ssh`) is replaced by a per-run codex permission profile (`codex_permission_args`): `:minimal` platform
  paths and the run directory readable, everything else denied, nothing writable; browser/computer-use/apps/plugins/hooks/image
  tools disabled. No credential copying is needed (codex still authenticates from `CODEX_HOME` in its own unsandboxed process).
  Verified with the real `codex sandbox -P` (local Seatbelt, no model call): run dir readable, `$HOME`, `/Users` and
  `~/.codex/auth.json` denied, writes denied; a `:root`-readable mutant makes `CodexSandboxProfileTests` fail. Residual: the
  guarantee depends on codex honouring permission profiles in `exec` the same way as in `codex sandbox` (same config path per
  codex source; not verified by a live model call), so `EGRESS_NOTICE` asks the owner to re-check after upgrading codex.
- Model names must start with an alphanumeric character so a model value can never be parsed as a flag.
