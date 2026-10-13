# Proving the method works — efficacy study protocol

You cannot prove efficacy from the code, from passing tests or from an agent
simulation. A functional demo — even a polished one with real sources — shows that
the *workflow runs*; it says nothing about a human learning more. Efficacy is an
empirical claim and needs real learners, **one** clearly defined comparison, fresh
outcome tasks and a **delayed** outcome.

What the system gives you is the measurement instrument: the append-only ledger
records every first attempt, its outcome, independence, scope, capability version
and timestamp, so a study produces numbers (`alearn efficacy --all`,
`alearn course export`) instead of opinion. This file turns that into evidence.

## 1. The claim, honestly stated
- **Design rationale (literature, not proof):** retrieval practice, spacing, worked
  examples with fading, and fresh transfer assessment are well-studied mechanisms.
  The local research synthesis (`learning-evidence-decisions.md`) records which
  specific studies were used, at what evidence strength, and what does **not**
  transfer to adult self-paced software learning. Citing them supports the design only.
- **To be tested:** that THIS implementation's *adaptation* (evidence-derived missions,
  method change, fresh-item exams, review queue) produces better **delayed** and
  **transfer** performance than the **same material and practice in a fixed sequence**
  for the same time on task.

Nothing below lets a pilot, a single positive effect size, or the app's own
process metrics be reported as "the method works".

## 2. Hypothesis (one comparison)
> For [topic], learners using the adaptive course score higher on a **delayed**
> (7–14 days) unassisted test built from fresh items than learners who study the same
> lessons, worked examples, practice tasks and feedback for the same time in a fixed,
> non-adaptive sequence.

- **Primary outcome:** delayed post-test score (one outcome; pre-registered).
- **Secondary:** transfer-form score, immediate post-test score, completion.
- A pure reading / self-study arm answers a different question ("does active practice
  help at all?"). If added, it is a **third arm with its own pre-registered
  hypothesis**, never the comparator for the primary claim.

## 3. Design
- **Randomized, two arms**, same topic, same total time budget:
  - **Adaptive (treatment):** roadmap → teach → guided practice → fresh exam →
    evidence-derived next mission, method change, fast-track, and the in-app review
    queue during the learning phase.
  - **Equal-time non-adaptive comparator (the only comparator for §2):** identical
    lessons, worked examples, practice items and feedback text, in a fixed order;
    no mission adaptation, no method change, no fast-track. It receives the **same
    number of re-exposure sessions** as the treatment's median review count, on a fixed
    schedule, so the contrast is adaptation, not "extra spacing".
- **Review vs delay window:** in-app reviews (first one ≥ 24 h after a pass) are a
  treatment component *inside the learning phase*. Between the end of the learning
  phase and the delayed test there is **no study and no review in either arm**. The
  24 h in-app review is not the outcome; the 7–14 day test is.
- **Within-subject alternative** (small samples): two matched sub-topics per learner,
  one per arm, counterbalanced order. The comparator arm still runs inside the app in
  fixed-sequence mode so both arms have a ledger; if it runs outside the app, process
  metrics exist for the treatment arm only and must be reported that way.
- Blind graders to arm. Free-text outcome answers: rubric + second blind grader on
  every pilot answer (report agreement, e.g. weighted kappa).
- **Fresh outcome forms:** immediate, delayed and transfer forms are parallel and
  unseen. Before use, screen every outcome item against the in-app item bank with the
  runtime's own identity check (`course_content.fingerprint` + `classify_freshness`):
  any `duplicate` or `needs_review` match is replaced, not argued away.

## 4. Participants and sample planning
- Comparable starting level: short pre-test; randomize within level (stratified or
  blocked). Use the pre-test as a covariate (ANCOVA).
- Normal-approximation sample sizes, α = .05 two-sided, 80 % power (Cohen 1988
  t-corrected values in brackets):

  | Design | Effect | Learners needed (before attrition) |
  |--------|--------|------------------------------------|
  | Two independent groups | d = 0.5 | ≈ 63 per group [64] |
  | Two independent groups | d = 0.3 | ≈ 175 per group |
  | Within-subject (paired) | d_z = 0.5 | ≈ 32 [34] |

  Minimum detectable effect at 80 % power: 12 per group → d ≈ 1.14; 24 per group →
  d ≈ 0.81; 24 paired → d_z ≈ 0.57. Power of 12 per group for d = 0.5 is ≈ .23.
- Planning assumptions, stated explicitly:
  - Against an **equal-time active comparator** expect smaller effects than against
    passive reading; plan with the smallest effect of interest (SESOI), e.g. d ≈ 0.3,
    not d = 0.5.
  - Inflate for delayed-test attrition: n / (1 − attrition) (15–30 % is common).
  - ANCOVA with pre-test correlation r reduces the required n by about (1 − r²);
    state the assumed r and check it in the pilot.
  - If learners are clustered (class, cohort), multiply by the design effect
    1 + (m − 1)·ICC.
  - Test reliability attenuates observed effects; report the outcome forms'
    reliability (e.g. Cronbach's α) and plan enough items for it.
- **A 12–24 learner study is a feasibility pilot only:** procedures, completion,
  timing, variance (with the upper confidence bound of the SD for planning — pilot
  effect sizes are biased upward and unstable), grader agreement and outcome-form
  reliability. It does not test the hypothesis.

## 5. Procedure
1. Consent (§9), pre-test, background.
2. Learning phase with a fixed time budget (`alearn study-plan`); in-app reviews per §3.
3. Immediate post-test (fresh form A).
4. **No study and no review** for 7–14 days, then the delayed post-test (fresh form B)
   — the primary outcome. Do not skip or shorten it.
5. Transfer test (fresh form C: new contexts, e.g. ranking → deduplication).

## 6. Measures
- **Outcome (external):** delayed, transfer and immediate scores from graded forms
  outside the app.
- **Process (ledger, `alearn efficacy`):** independent-success rate, attempts to first
  strong success, assisted → independent transitions, review outcomes. Process metrics
  explain *how* learners used the system; they are never the outcome. Only evidence
  bound to the **current capability version** counts (superseded capabilities are
  history). Review evidence counts as delayed only when the ledger itself shows the
  spacing (`review.counts_as_delayed`); the app never accepts an elapsed time from a
  caller.

## 7. Analysis
- **Pilot:** descriptive statistics, confidence intervals and the feasibility
  criteria pre-registered in §10 — no significance test of the hypothesis and no
  "success" threshold on d.
- **Confirmatory study:** pre-register the SESOI, primary outcome, ANCOVA model and
  power calculation from the pilot's variance; report effect size with CI and the
  pre-registered test. One primary outcome; secondary outcomes are labelled as such
  (correct for multiplicity if more than one is tested).

## 8. Threats to validity (address or disclose)
- **Synthetic runs and demos do not count** — agent, simulation and DEMO sessions are
  excluded; `alearn efficacy` is only meaningful on real-learner ledgers.
- **Evidence strength is capped consistently with the runtime:** in the course runtime
  selected-response items are recognition evidence (weak), other deterministic
  non-code items and rubric-judged open answers are at most medium, and a single
  rubric judgment never passes a node. Only Python items with hidden checks on
  automatically fresh items can be strong. Report results per assessment kind.
- Grader reliability for open answers: rubric + second blind grader; report agreement.
- In-app logs cannot rule out external help (books, other apps, people).
- Novelty/Hawthorne: the comparator must be equally engaging and equal-time.
- Small n: a pilot is a pilot; do not generalize beyond it.

## 9. Consent and privacy
- Informed consent before the pre-test: what is recorded (answers, check results, help
  requests, in-app active/idle time), where it stays (the learner's device; exports only
  with permission), how long, and the right to withdraw and have data excluded.
- Analyse pseudonymised exports (`alearn course export`), never raw workspaces; do not
  collect source texts, books or credentials. Time and scores are never used to judge
  effort, intelligence or responsibility.

## 10. What counts as a result
- **Functional readiness** (what the repository's tests, E2E and demos show): the
  workflow runs, guards hold, evidence is recorded honestly. This is **not** efficacy.
- **Pilot result:** feasibility criteria met (pre-registered, e.g. ≥ 80 % complete the
  delayed test, outcome-form reliability ≥ .7, grader agreement ≥ .6) plus effect
  estimates with wide CIs. A positive pilot effect size is a reason to run the
  confirmatory study, not a claim that the method works.
- **Efficacy claim:** only from a pre-registered, adequately powered randomized study
  against the equal-time non-adaptive comparator on the delayed, fresh, unassisted
  outcome — and only for the population, topic and setting studied.
