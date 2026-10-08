# Attributed rubric assessment

`rubric-check` validates an assessor's criterion scores and preserves the reasons.
It does not read meaning automatically, authenticate the named reviewer, or certify
mastery. A human or host agent must inspect the actual work and judge the rubric.
Examples below illustrate the data format, not a sufficient real-world rubric.

Save `submission.txt` as the following exact text, without a final newline:

```text
The evidence supports this conclusion, with uncertainty.
```

Save `rubric.json`:

```json
{
  "pass_threshold": 0.8,
  "criteria": [
    {
      "id": "reasoning",
      "description": "Tie the conclusion to evidence",
      "max_score": 4
    },
    {
      "id": "uncertainty",
      "description": "State a limitation",
      "max_score": 1,
      "critical": true
    }
  ]
}
```

Save `assessment.json`:

```json
{
  "reviewer": "Teacher or named host agent",
  "submission_sha256": "125c8d722282f8b6a9022982f2fbfb501b9e195ba20a22e79d1520f13b426202",
  "scores": [
    {
      "id": "reasoning",
      "score": 4,
      "rationale": "The claim is tied to evidence.",
      "evidence_excerpt": "evidence supports"
    },
    {
      "id": "uncertainty",
      "score": 1,
      "rationale": "Uncertainty is acknowledged.",
      "evidence_excerpt": "uncertainty"
    }
  ]
}
```

Run from the chosen workspace (all input paths must stay inside it):

```sh
alearn --workspace . rubric-check --session <id> --capability <node-id> \
  --submission-file submission.txt --rubric-file rubric.json \
  --assessment-file assessment.json --attempt-id explanation-1 \
  --independence unassisted
```

For a real submission, compute SHA-256 over its UTF-8 text bytes, including its
actual line endings, and replace `submission_sha256`. It binds the assessment to
that exact version. The runtime rejects missing or duplicate criteria, unknown
scores, non-finite or out-of-range numbers, missing rationales, and positive scores
without an excerpt found in the submission. A zero score may use an empty excerpt
when the required content is absent; explain the omission in the rationale.

A critical criterion below the pass threshold prevents an overall pass regardless
of the weighted total. Outcome and criterion details are persisted as attributed
`self_report` evidence capped at medium. Inventing a reviewer name, copying source
terms, or repeatedly submitting the same report never establishes mastery.
