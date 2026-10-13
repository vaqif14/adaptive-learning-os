# Learning-evidence decisions (v7.1)

This record documents how the v7.1 remediation used research and review. It is a
decision log for the owner, not a claim of efficacy (see `efficacy-study.md`).

**Who reviewed.** Four perspectives — learning design (LD), assessment/psychometrics
(AP), UX/accessibility (UX) and architecture/reliability (AR) — were run as separate
**AI analytical lenses** (independent agents over the same code at `df88a0c`). They are
not human experts; their agreement is not independent validation, and no claim here
is peer-confirmed beyond what the cited sources themselves report. No human reviewer
was consulted.

**What was searched.** Primary sources from China, Japan and Denmark were fetched by
three research agents on 2026-10-12. Many were readable only at abstract or repository
level; every row says what was and was not retrieved. Country of origin is context for
population and setting only — it is not used as a proxy for learner traits.

**How decisions were made.** *adopt* = use as evidence-backed; *adapt* = take only a
qualitative design rule; *reject* = do not use as support for any feature or claim.
No row is *adopt*, and no source is used to justify a numeric parameter: every
interval and threshold in the code is a labelled, policy-versioned heuristic.

## Research decision table

All 13 sources were fetched, but several only at abstract level (see the Design and evidence column). Four AI lenses rated each row: learning design (LD), assessment psychometrics (AP), UX/accessibility (UX) and architecture/reliability (AR). The decision column is my synthesis of the four ratings, not a vote. Agreement among the lenses is not proof, and effect sizes are unverified wherever the row says so.

Decision key: adopt = use as evidence-backed; adapt = take only the qualitative design rule; reject = do not use as support for any feature or claim. No row is "adopt". No source supports a hard-coded numeric parameter.

| Practice | Country, population, context | Design and evidence strength | Transfer limits | Decision | Component changed | Lens disagreements |
|---|---|---|---|---|---|---|
| Retrieval practice vs restudy of worked examples (math word problems), immediate vs 1-week delayed test. Huang et al. 2023, Front. Psychol. 14:1093653. https://doi.org/10.3389/fpsyg.2023.1093653 (verified by fetch) | China (mainland). University students, Minnan Normal Univ.; Exp1 n=120, Exp2 n=120 (111 retained), Exp3 n=80; lab word problems | 3 randomized between-subjects experiments, n=20 per cell. Null result for the delayed test (Exp1 group F(2,118)=0.493, p=0.612, ηp2=0.008). Moderate. | Small cells, 1-week maximum, students, procedural problem solving only (not declarative recall), single research group | Adapt, as a caution rather than a parameter. Tag items declarative vs procedural. A re-asked worked-example step is not delayed evidence. Delayed checks on procedures use fresh, varied items. | `course_content.py` item model and `validate_items`; review queue (F5); F1 freshness keyed on content fingerprint, not `item_id`; Practice/Assessment task labels | No disagreement on the decision (4 adapt). The lenses differ on mechanism: AP and AR tie it to the F1 content fingerprint, UX to labelling task types in the UI. |
| Spaced practice frequency vs exam grades (C programming). Zhang et al. 2017, ICCE proceedings. https://library.apsce.net/index.php/ICCE/article/view/2280 (verified by fetch) | China (mainland). 69 first-year students, Beijing Normal Univ., self-selected timing | Correlational, no randomization. Frequency predicted homework (p=0.001) and midterm (p=0.023); volume did not. Effect sizes not retrieved. Limited. | Confounded by conscientiousness; conference paper; n=69; instructor-led C course; midterm outcomes only | Adapt, as a display and nudge feature only. It must not feed mastery, the exam lock or any interval. | Dashboard / Roadmap progress view (distinct practice days next to time); timestamped practice events | No disagreement (4 adapt). UX adds a constraint: no streak pressure or guilt copy. |
| 3-day vs 7-day multiple-choice programming practice via mobile quiz. Li et al. 2021, J. Pacific Rim Psychol. 15. https://doi.org/10.1177/18344909211008264 (verified by fetch, abstract level) | China (mainland). 200 first-year novice programming students, credit course, mobile "Daily Quiz" | Between-subjects; randomization not verified. The 3-day group did better on the final exam and on first-attempt accuracy. Effect sizes and p-values not retrieved. Moderate. | MCQ only; exam incentive; frequency is confounded with gap length; cannot identify an optimal gap; does not test code production | Adapt. The first review gap is a configurable default in days, not "3 days is optimal". Record first-attempt correctness. Unassisted production items remain the counting evidence. | Durable per-capability review queue (F5, reuse `session.py` `due_queue`); Roadmap "due for review" badge | No disagreement (4 adapt). AP stresses that MCQ recognition is weak evidence for code production. |
| Delay and corrective feedback in the testing effect (English-Chinese word pairs). Wang & Zhao 2019, Swiss J. Psychol. 78(3-4):125-136. https://doi.org/10.1024/1421-0185/a000225 (verified by fetch, but the DOI conflicts with a search snippet showing a000229; confirm before citing) | China (mainland). University-age participants, ECNU repository; lab, L2 vocabulary; n not retrieved | Two experiments. Testing helped at the 20-min delay but not at 0 or 40 min. With feedback and a 24 h final test it helped at all delays, more with longer gaps. No sample size or effect size retrieved. Limited. | Word pairs, minutes to 1 day, lab; declarative memory, not technical concepts or procedures | Adapt, as qualitative rationale only: always give corrective feedback after a retrieval attempt, and do not quiz immediately after explanation (soft default). No numbers taken. | `course.py` attempts and `start_exam` flow; feedback rendering in Practice/Assessment (polite live region); first retrieval at the next session start | No disagreement (4 adapt). AP adds that exam feedback must not leak item content. |
| Teaching with variation (bianshi), long-run reform. Gu 2022 ICME-14 plenary preprint. https://www.mathunion.org/fileadmin/ICMI/Conferences/ICME/ICME 14/Proceedings/Plenary Lectures and Panels/PL Lingyuan Gu.pdf (verified by fetch) | China (mainland). Primary and junior-high pupils, Qingpu County, Shanghai, 1977-2022 | Multi-decade field reform, not randomized; a bundled package, so variation is not isolated. Pass rate 16% (1979) to 85% (1986). Very limited. | Children, state school system, mathematics only; confounded by teacher development, curriculum and time trends; conference preprint | Reject as a feature basis. It is an untested authoring hypothesis to test locally, with no effect claim. | None. At most a note in authoring guidance for lesson and item variants (`course_content.py`), alongside the F1 fresh-item requirement. | Disagreement. LD, AP and UX rated it unsure and AR rated it reject. I side with reject because three of the four "unsure" ratings also say "do not build on it". UX flags the accessibility cost of side-by-side contrast. |
| Expanding vs equally spaced retrieval, delayed recall. Kanayama & Kasahara 2016, ARELE 27:217-232. https://doi.org/10.20581/arele.27.0_217 (verified by fetch via J-STAGE) | Japan. Japanese learners, L2 English vocabulary, 20 words, delayed test at Day 43; n=53 (34 vs 19) | Two-group comparison; assignment method not confirmed. No long-term difference between schedules once both had four sessions. Limited. | Small, unbalanced groups; rote L2 words; about 6-week horizon | Adapt. Use a simple configurable ladder with several spaced exposures and a late unassisted check. Do not build an expanding-interval algorithm. The 4 sessions and 3 weeks are heuristics to calibrate, not findings. | Review scheduler (F5, reuse `session.py` `due_queue`); schedule stored as data; UI shows "exposures completed of N" | No disagreement (4 adapt). It is a low-priority tunable. |
| Self-explanation after worked ratio problems, with transfer test. Tajika, Nakatsu & Nozaki 2006, Aichi Univ. of Education Bulletin 9:9-16. http://hdl.handle.net/10424/1319 (verified by fetch via https://aue.repo.nii.ac.jp/records/2146; abstract-level; peer review not verified) | Japan. 73 sixth graders, ratio word problems | Three-group experiment; randomization not confirmed. Self-explanation and self-study groups beat control on transfer; high explainers beat low explainers (correlational). No effect sizes. Limited. | Children aged 11-12; school mathematics; bulletin of unverified review status; adults differ in prior knowledge | Adapt. Add an optional self-explanation prompt before assisted practice. It is an ungraded reflection and never mastery or exam evidence. Any check uses fresh transfer items. | Lesson and assisted-practice flow (`course_content.py` lesson schema; Lesson.jsx labelled textarea); transfer item in `start_exam` | Disagreement on scoring and on the decision. LD would score explanation quality as a formative signal. AP and UX say it must stay unscored because free-text validation is blocked by F4 (Python-only validation). AR rated it unsure and left it to the pedagogy lens. I side with unscored. |
| Flipped programming course with learning analytics. Iio 2024, J. Inf. Process. 32:159-165. https://doi.org/10.2197/ipsjjip.32.159 (verified by fetch via J-STAGE, abstract only) | Japan. First-year university students, Chuo Univ., AY2021, instructor-led | Observational LMS analytics, no control group. No quantitative result retrieved; n not stated. Very limited. | Single course and instructor; correlational; not self-paced; young adults | Reject. It changes nothing in the learning loop. Attempt, hint and time telemetry needs no citation, and hints must be flagged as assisted evidence. | None | No disagreement (4 reject). UX notes that extra telemetry is a privacy decision for a local-first system. |
| Flipped programming, offline (2019) vs online (2020). Hirata 2022, Int. J. Innov. Learn. 31(3):385-403. https://ideas.repec.org/a/ids/ijilea/v31y2022i3p385-403.html (verified by fetch; DOI not confirmed) | Japan. University programming students, two cohorts | Survey of perceptions, not outcomes. 34% more students called the online course challenging. n not stated. Very limited. | Perception only; pandemic confound; one institution; instructor-led remote course | Reject as evidence. The hint ladder and stuck-path rationale stand on design logic alone. | None as evidence. UX notes the existing non-empty hint-ladder rule (`course_content.py:159-166`) and a visible "I am stuck" path are justified independently. | Disagreement. LD, AP and AR rated it reject; UX rated it adapt, as design motivation for hint ladders. I side with reject as evidence. UX's point is kept as a design-logic note. |
| Teacher's aides, cluster RCT. Andersen, Beuchert, Nielsen & Thomsen, J. Eur. Econ. Assoc. https://doi.org/10.1093/jeea/jvy048 (verified by fetch via the VIVE page; year and author order unreconciled: VIVE says 2018 with Beuchert first, a search result says 2020) | Denmark. 6th graders (about 13), reading and math; about 105 schools and 5,213 students per a search snippet only | Cluster RCT. Positive test-score effects, larger for aides without teaching degrees spending more time in class; effect more than class-size reduction. Strong design, but effect sizes not retrieved. | Human aides in compulsory school; not adult, self-paced or software; no test of hints, delayed review or unassisted assessment | Reject. The strong design does not transfer to this construct. Help gating (F3) rests on the system's own evidence-lock logic. | None | No disagreement (4 reject). All lenses warn it would borrow evidence strength falsely. |
| Added instruction time plus test information to teachers. Andersen, Guul & Humlum 2019, HCEO WP 2019-049. https://humcap.uchicago.edu/RePEc/hka/wpaper/Anderson_Guul_Humlum_2019_instruction-time-information-achievement.pdf (verified by fetch; registry page returned HTTP 500; working paper, journal version not confirmed) | Denmark. Grade 5 (about age 11), Danish reading, low vs high SES | RCT field experiment. Improved low-SES reading, narrowed the SES gap, teachers updated beliefs. The 0.15 SD figure is from a search summary and unverified; n not retrieved. Moderate. | Children; the mechanism is teachers updating beliefs; time and information are bundled; reading only | Adapt, as indirect rationale only: regular unassisted measurement drives what is practised next. Do not cite the 0.15 SD figure. | Learner model and next-action selection (`session.py` `next_action`, `course.py` next_mission); dashboard labels assisted vs unassisted results; evidence bound to capability version (F2) | No disagreement on the decision (4 adapt). The lenses differ on application: AR links it to F2 and F5, UX to the dashboard readout. |
| Early tutoring for at-risk readers, RCT. Seerup, Dietrichson, Jensen & Bingley 2025, J. Educ. Students Placed at Risk. https://www.vive.dk/da/udgivelser/tutoring-young-children-at-risk-of-developing-reading-difficulties-effects-of-an-early-tutoring-intervention-on-literacy-skills-and-executive-functions-2z1p38jz/ (verified by fetch of the VIVE page; DOI not seen) | Denmark. 6-7 year-olds at risk, 8 schools | RCT. Primary analysis not significant; exploratory gains; verbal memory negative; implementation variation noted. Limited. | Very young children, early literacy, small sample; almost no adult transfer | Reject. It supports no feature. The only lesson, to track fidelity and not over-claim from small pilots, is already covered by the evidence locks and audit. | None | No disagreement (4 reject). |
| Problem-based learning vs courses in engineering. Faria da Silva 2016, CISPEE proceedings. https://vbn.aau.dk/en/publications/an-analysis-of-the-implementation-of-problem-based-learning-at-th (verified by fetch; no DOI; title partly reconstructed from the URL slug) | Denmark. Aalborg Univ., Dept. of Energy Technology, six years of grades plus a survey | Observational grade comparison and perception survey, no selection control. Projects had higher grades and lower failure rates. n not stated. Very limited. | Supervised group projects; grades not comparable across assessment types; self-reported perceptions; not individual or self-paced | Reject. Group-project grades can mask individual mastery. That argues for keeping the exam lock on individual unassisted evidence, which the system already does. | None | No disagreement (4 reject). |

### Unresolved uncertainty

- **Source verification depth.** Every row is marked fetched, but most only at abstract or repository-page level. Sample sizes, effect sizes and peer-review status are missing for Wang & Zhao, Tajika et al., Iio, Hirata, both Andersen papers and Faria da Silva. Do not cite any figure the row marks as not retrieved.
- **Citation errors to fix before any citation.**
  - Wang & Zhao DOI: a000225 (repository) vs a000229 (search snippet).
  - Andersen et al. (JEEA): year (2018 vs 2020) and author order.
  - Hirata: DOI not confirmed.
  - Faria da Silva: title partly reconstructed.
  - Seerup et al.: no DOI seen.
  - Andersen, Guul & Humlum: only a working paper was confirmed.
- **Li et al. 2021 assignment.** Whether the 3-day vs 7-day groups were randomized was not verified. Its contrast confounds frequency with gap length, so it cannot support an optimal interval.
- **No source supports a numeric constant.** The intervals (1, 3, 7, 21, 60 days), the "about 4 exposures" and "3 weeks" figures and the similarity thresholds (0.3, 0.5, 0.6, 0.8) proposed by the lenses are design heuristics, not findings. They need calibration on real item pairs and local data.
- **Population gap.** None of the 13 sources studied adult self-paced learners using software. Every transfer is by analogy.
- **Remaining lens disagreements.**
  - Gu: reject vs unsure.
  - Tajika: scored formative signal vs unscored.
  - Hirata: UX alone says adapt.
  - Whether the delayed retrieval gap is a soft default or a rule: AP says soft default; UX proposes next-session-start as the first retrieval; LD, AP and AR each propose different interval ladders.
- **Contradictions in the lens reviews.** The efficacy protocol conflicts with the code on non-code evidence strength: the protocol calls it "medium by design", while `evidence_semantics.FORMAT_MAX_STRENGTH` allows strong for explanation, scenario_performance and oral_defense. This is unresolved.
- **Pilot power.** The efficacy pilot's pre-registered d>=0.4 is not detectable at 12-24 learners (MDES about 0.81-1.14). A pilot is feasibility evidence only, not a claim that it works.
- **Rubric-graded evidence.** LD and AP differ on whether host-agent rubric grading can reach "strong" strength, and on the free-text criteria thresholds. The caps and thresholds are proposals, not settled.

### Note on the four perspectives

The four perspectives (learning design, assessment psychometrics, UX/accessibility, architecture/reliability) are AI analytical lenses run as separate agents. They are not human experts, and their agreement is not independent validation. Decisions above are a synthesis for the owner's review, and no claim in this table should be treated as peer-confirmed beyond what the cited source itself reports.

## Audit findings at df88a0c and their disposition

All four lenses independently confirmed that F1–F5 were still present at `df88a0c`
(code reading); each was then reproduced by a failing regression before its fix
(`tests/test_v710_audit_regressions.py`: 11 failed, 1 control passed on the
unmodified runtime).

| ID | Defect (audited) | Fix in v7.1 | Verification |
|----|------------------|-------------|--------------|
| F1 | Freshness by `item_id`; renamed/reordered/cosmetic copies counted as fresh | Identity-independent content + behaviour fingerprints (`course_content.fingerprint`), durable `exposure_records`, `classify_freshness` (duplicate / needs_review / fresh), explicit `item-freshness` decision (evidence then capped at medium), sibling copies rejected, `task_fingerprint` = content fingerprint + submission | 4 regressions + 3 hardening tests; E2E duplicate check |
| F2 | Approval did not invalidate progress bound to a changed capability | `capability_version` per node; impact preview at propose, applied at approval (superseded passes kept as history, reviews invalidated, dependents flagged, old lesson/items no longer ready/eligible); v1→v2 migration with legacy bindings | 2 regressions + 5 impact tests + 3 migration tests; E2E reapproval |
| F3 | Stale/invalid grounding still taught; cache replaced before commit | Content-addressed source objects; per-lesson source-hash binding re-verified on every use; `grounding_invalid` blocks help/teaching/practice/exam/readiness; `reground` only when every quote re-verifies; labelled historical snapshot | 3 regressions + 4 failure-mode tests (unreadable, identity_mismatch, partial, missing, changed-valid, quote gone, lost object, restart, reapproval); E2E |
| F4 | Python-only assessment | `kind` dispatch: concept/scenario (deterministic key, distractor-diagnosed misconceptions), free_text (waits for a quote-linked rubric judgment; never keyword-scored; one judgment is provisional); same guards, lock, persistence and UI | 2 regressions + 6 hardening tests + bridge test; E2E concept path; real NVIDIA RAG demo |
| F5 | Review existed only as text in one mission | Durable `review_queue` (deterministic ids, prerequisite-first order, lapse retry, abandon keeps due, invalidation), `start_review`, due-review mission/turn/driver dispatch, injectable clock; spacing claims only from ledger timestamps | 1 regression + 5 hardening tests; E2E due-review path |

### Additional lens findings

| Lens | Finding | Disposition |
|------|---------|-------------|
| AR (high) | Source cache mutated in place before the manifest commit | **Fixed** (content-addressed objects; interrupted-commit regression) |
| AR (medium) | Wall clock everywhere; no injectable clock | **Fixed** (`CourseKernel(clock=…)`) |
| AR (medium) | Superseded lesson/item versions overwritten on disk | **Fixed** (immutable `archive/<version>.json` copies; orphan files still ignored) |
| AR (low) | A rebuild-from-defaults migration would drop unknown keys | **Fixed** (`_v1_to_v2` deep-copies; unknown-key test; pre-v2 archive in the same transaction; `down_v2_to_v1` export archives v2 data) |
| AP (high) | Replay across renamed items not collapsed in evidence | **Fixed** for item identity (content fingerprint in `task_fingerprint`); trial grouping remains per session by design |
| AP (high) | No item-to-item / exposed near-duplicate screen | **Fixed** (F1) |
| AP (medium) | Efficacy "delayed" metric could count unobserved spacing | **Fixed** (`review.counts_as_delayed` from ledger timestamps; protocol updated) |
| AP (medium) | Transfer scope is asserted by the author, not measured | **Open** — documented limitation; freshness checks identity, not transfer distance |
| AP (medium) | One strong item can mark readiness; no multi-item floor | **Open** — mastery policy outside this change; reported, not altered |
| LD (medium) | Thin remediation ladder (no step-back to prerequisites, no fading/completion problems) | **Open** — mission reasons rewritten in plain Azerbaijani; ladder redesign not in scope |
| LD (low) | Mission reason was a technical tally | **Improved** (plain lead sentence + tally + review date) |
| LD (low) | One worked example, no completion step | **Open** |
| UX (medium) | E2E unconditional passes, unverified screenshots, bare waits, no CI job | **Fixed** (typed checks only, PNG/size/sha256 verification, retained trace, synthetic fixtures, CI `browser-e2e` job — CI execution unverified until pushed) |
| UX (medium) | Exam-confirm dialog without focus trap/Escape/focus return | **Fixed** (+ inert background; E2E keyboard checks) |
| UX (medium) | Sticky header covered headings | **Fixed** (scroll padding; E2E geometry check) |
| UX (low) | Disabled locked actions unreachable for keyboard/screen readers; lock note not announced | **Fixed** (`aria-disabled` focusable buttons with described reason; `role=status`) |
| UX (low) | Stale vs source labels relied on colour/suffix | **Superseded** — stale material is never shown as current (fail closed + labelled historical snapshot) |
| UX (low) | Self-explanation minimum length not explained | **Open** (low) |
| Own E2E finding | A roadmap revision dropped the built app (`frontend/dist`) under a running server | **Fixed** (dist carried forward; mutation-checked regression) |
| Own contract finding | Bridge contract changed without a UI schema bump | **Fixed** (`UI_SCHEMA_VERSION` 2; stale v1 builds refused with the existing message) |

## Decisions taken on the open points above

- **Non-code evidence strength** (protocol vs `FORMAT_MAX_STRENGTH` contradiction):
  resolved in the course runtime and the protocol together — selected-response items
  are `recognition_mcq` (weak); other deterministic concept/scenario items are capped
  at medium; rubric-judged open answers are `explanation`, medium, `correctness_checked:
  false`. The generic evidence semantics are unchanged for non-course paths.
- **Rubric-graded open answers** (LD vs AP/UX disagreement): one judgment never passes
  a node (`provisional`, mission `confirm_with_fresh_item`); a pass needs a second
  independent open answer judged with at least medium confidence, or a deterministic
  item for the same capability version. Every "met" criterion must quote the learner's
  own response; no keyword scoring exists in the runtime.
- **Self-explanation** (Tajika et al. disagreement): stays unscored and formative.
- **Review intervals:** the first review is due one day after a first independent pass
  and doubles after each successful review; a lapse shortens it
  (`scheduling.half_life_days`, the policy the session driver already used). These are
  labelled heuristics; the UI says so. The study's delayed outcome (7–14 days) is
  separate from in-app reviews.
- **Near-duplicate thresholds** (check-set overlap ≥ 0.5, word-shingle similarity ≥ 0.5,
  5-gram similarity ≥ 0.6) are policy `fresh-v1` heuristics; uncertainty yields
  `needs_review`, never freshness. Calibrate on real item pairs before changing them.

## Second review round: adversarial review of the v7.1 diff

Four AI lenses reviewed the uncommitted v7.1 diff; an independent skeptic agent then
tried to refute each finding (reading the code path or reproducing it in a temp dir).
31 findings → 22 confirmed, 9 rejected. Every confirmed finding was fixed and has a
regression in `tests/test_v710_review_fixes.py` (19 tests) or the browser E2E; each of
the 19 regressions was shown to turn red when its fix is reverted (mutation check on a
scratch copy).

| Lens | Confirmed finding | Fix |
|------|-------------------|-----|
| state | Due review of a dependent surfaced although its prerequisite pass was superseded (unstartable) | due reviews require the node to be in the plan and available; the entry stays scheduled |
| state | Legacy migration with a pending removal never superseded the removed node | absent nodes with history bound as `legacy_removed`; approval supersedes them |
| state | Legacy binding trusted a drifted on-disk roadmap | binding treats drift as `legacy_unverified`; impact lists `unverified` nodes in the approval panel |
| state | `down_v2_to_v1` + re-upgrade re-bound stale lessons/items | `_v1_to_v2` restores `v2_archive`; only additions are bound afresh |
| state | Proposing the approved version again left the course awaiting approval | review cleared, previous phase restored |
| state | An idempotency key replayed with different arguments | key bound to a digest of the request; mismatches refused |
| assessment | Unverifiable (legacy) exposures never blocked freshness | any unverifiable exposure makes a fresh item `needs_review` |
| assessment | Practice could contain an exam question/check verbatim | practice/worked example compared by behaviour signatures and full item surface |
| assessment | Reworded sibling (same options/scenario) counted as fresh | similarity uses all learner-visible text (prompt, scenario, stems, options) |
| assessment | A low-confidence rubric review counted as retention | `retention: uncertain`, same interval (`retry_uncertain`) |
| assessment | An ordinary exam on a passed node reset the review schedule | no new first review while one is pending |
| assessment | Spacing measured at finalize | measured from the ledger's start event of the review attempt |
| reliability | CRLF source text could never ground | cached text hashed as bytes |
| reliability | A failed install commit could hide the committed lesson/items | committed versions read from the immutable per-version archive |
| reliability | Huge integer answer → uncaught `OverflowError` | bounded numeric parsing; bridge maps it to 400 |
| reliability | Failed practice commit + retry duplicated evidence | deterministic practice evidence id |
| reliability | Malformed rubric misconception list → `TypeError` | `rubric_required` refusal |
| reliability | Exports changed the parent directory's permissions | create-only 0600 export, parent untouched |
| UI | `inert=""` was a no-op under React 19 | boolean `inert` on one wrapper; focus restored after it is lifted (E2E-checked) |
| UI | Abandon confirmation dropped focus | focus to the safe choice, Escape cancels, focus returns (E2E-checked) |
| UI | Busy state used real `disabled` and dropped focus | busy uses `aria-disabled` + `aria-busy` |
| UI | Out-of-order refreshes could apply an older state | request sequence guard |

Rejected by the skeptic but cheap and kept anyway: refusal codes asserted in tests
instead of bare exception types, CI E2E job timeout + pinned Playwright, free-text
minimum length gated in the UI, pending-state reasons on locked actions, browser
coverage of the free-text/rubric-pending path. Still rejected/open: one quote may
support several criteria (graders are accountable for their judgment), a
selected-response pass unlocks the next node (documented as recognition-level), no
npm lockfile for the template (CI installs within the declared ranges).
