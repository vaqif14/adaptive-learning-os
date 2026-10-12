# Proving the method works — efficacy study protocol

You cannot prove efficacy from the code or from a two-agent simulation. A synthetic
tutor + synthetic learner only proves the *workflow runs*; it says nothing about a
human learning more. Proof is an empirical claim and needs three things the code
alone can't give: real learners, a **comparison group**, and a **delayed** outcome.

What the system gives you is the measurement instrument. The append-only ledger
records every first attempt, its outcome, whether it was independent, its scope and
timestamp — so a pilot produces hard numbers (`alearn efficacy --all`) instead of
opinion. This file is how to turn that into evidence.

## 1. The claim, honestly stated
- **Grounded (already supported by the literature):** the *mechanisms* this OS
  enforces are among the best-evidenced in learning science — retrieval practice /
  the testing effect (Roediger & Karpicke 2006), spaced review (Cepeda et al. 2006),
  worked-example→fading (Sweller), productive struggle, and transfer-based
  assessment. Citing these supports the *design*.
- **To be proven (your pilot):** that THIS implementation, with real learners on
  your material, produces better **retained, transferable** learning than the normal
  alternative (reading / passive study) for the same time on task.

Do not claim more than the pilot shows.

## 2. Hypothesis
> For [topic], learners who study with the Adaptive Learning OS method score higher
> on a **delayed** (1–2 week) retention + transfer test than learners who study the
> same material for the same time by reading/self-study.

Primary outcome: delayed test score. Secondary: transfer items, time-to-competence.

## 3. Design
- **Randomized, two groups**, same topic, same total time on task:
  - Treatment: the adaptive course (roadmap → teach → guided practice → fresh exam →
    evidence-derived next mission, with spaced review).
  - **Equal-time non-adaptive comparator** (needed to estimate the *adaptation*
    benefit, not just "studying vs not"): the same lessons, worked examples,
    practice tasks and feedback text, in a fixed sequence, with no mission
    adaptation, no method change and no fast-track. A pure reading control answers a
    different question (does any active practice help?) and may be added as a third arm.
- **Within-subject alternative** (if few people): each learner studies two matched
  sub-topics, one per condition, counterbalanced order.
- Blind the person who grades the post-tests to which group produced each answer.
- Outcome tasks are **fresh and unassisted** for every learner: none of them may appear
  in lessons, worked examples, practice or in-app exams. Use three parallel forms:
  immediate post-test, delayed post-test (7–14 days, no review in between) and a
  transfer form (new contexts, e.g. ranking → deduplication).

## 4. Participants and sample planning
- Comparable starting level; measure it with a short **pre-test** and randomize within
  level (stratified or blocked randomization).
- Do **not** assume 12–24 learners can establish a medium effect. With α = .05
  (two-sided) and 80 % power (normal approximation; Cohen 1988 tables give the
  t-corrected values in brackets):

  | Design | Effect | Learners needed |
  |--------|--------|-----------------|
  | Two independent groups | d = 0.5 | ≈ 63 per group [64] |
  | Two independent groups | d = 0.3 | ≈ 175 per group |
  | Within-subject (paired) | d_z = 0.5 | ≈ 32 [34] |

  Minimum detectable effect at 80 % power: 12 per group → d ≈ 1.14; 24 per group →
  d ≈ 0.81; 24 paired → d_z ≈ 0.57. Power of 12 per group to detect d = 0.5 is only ≈ .23.
- Therefore a 12–24 learner study is a **feasibility pilot**: check procedures,
  measure variance, completion and test reliability, and estimate effect sizes with wide
  confidence intervals. Plan the confirmatory study from the pilot's variance, with the
  smallest effect that would matter in practice, and pre-register it.

## 5. Procedure
1. Pre-test (baseline) + background.
2. Learning phase, fixed time budget per `alearn study-plan`.
3. Immediate post-test.
4. **Delayed post-test after 1–2 weeks with no review** — this is where retrieval +
   spacing are expected to win; it is the decisive measure. Do not skip it.
5. Transfer test: novel problems, not the practiced items.

## 6. Measures
- **Outcome (external, the proof):** delayed-test score, transfer-test score. These
  come from your graded tests, not from the OS.
- **Process (from the ledger, `alearn efficacy`):** independent-success rate,
  attempts-to-first-strong, assisted→independent transitions, transfer-scope strong
  successes, learning curve. These explain *why* and track engagement; they are not
  themselves the outcome.

## 7. Analysis
- Compare groups on the delayed/transfer scores: **effect size (Cohen's d)** plus a
  significance test; report confidence intervals. For the within-subject design, a
  paired test.
- Pre-register the success criterion before running (e.g. d ≥ 0.4 on delayed retention).

## 8. Threats to validity (address or disclose)
- **Synthetic runs don't count** — agent/simulation sessions must be excluded;
  `alearn efficacy` is only meaningful on real-learner ledgers.
- Grader reliability for free-text answers: use a rubric + a second blind grader on a
  sample; non-code mastery is medium evidence by design (see evidence trust).
- Novelty/Hawthorne effect: keep control engaging and equal-time.
- Small n: call it a pilot; don't generalize beyond it.

## 9. Consent and privacy
- Informed consent before the pre-test: what is recorded (answers, check results, help
  requests, in-app active/idle time), where it stays (the learner's device; exports only
  with permission), how long, and the right to withdraw and have data excluded.
- Analyse pseudonymised exports (`alearn course export`), never raw workspaces; do not
  collect source texts, books or credentials. Time and scores are never used to judge
  effort, intelligence or responsibility.
- Report that in-app logs cannot rule out external help (books, other apps, people).

## 10. Minimum credible result
A single small randomized pilot showing a positive effect size on the **delayed +
transfer** test, with the process metrics consistent, is a defensible "it works
here" claim. Anything short of a delayed, comparison-based measure is not proof.
