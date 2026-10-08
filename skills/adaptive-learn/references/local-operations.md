# Local Codex/agent operations

Supported deployment: one user's local workspace on macOS or Linux, Python 3.11+,
on a filesystem providing reliable `flock`, atomic rename and `fsync`. Multiple
local agent/CLI processes may share a session. This is not a hosted multi-tenant
service. The optional HTTP server is unnecessary for the Codex workflow.

## Health and integrity

```sh
alearn --workspace . local-doctor
alearn --workspace . ledger-verify --session <id> --force
```

`local-doctor` checks durable writes, lock support, packaged assets and existing
session journals/metadata. Failure exits 2. It replays committed state updates if
an earlier operation stopped before materializing all JSON views. It does not
test learning effectiveness, authenticate rubric reviewers, inspect every
external toolchain or certify a network filesystem.

Session operations serialize across threads and processes, including CLI evidence
writes and backup snapshots. Cards, contract changes, notebook selection and
roadmap installation commit to the hash-chained journal before updating their
JSON views. A process exit after commit is recoverable on the next operation.
An error after commit may mean the operation committed; inspect the session
before retrying a non-idempotent action such as `add-card` or `review-card`.

Roadmaps are fully prepared before installation. Replaced workspaces remain in
`workspace-prev-*`, including learner submissions. A failed preparation may leave
an uncommitted `workspace-stage-*` directory for diagnosis; it does not replace
the active workspace. Do not edit runtime JSON views by hand; use CLI commands.

## Backup and restore drill

Run from the source workspace, using a new archive name:

```sh
alearn --workspace . backup-session --session <id> --output ./backups/session.zip
```

Archive contents include the journal, metadata, cards, roadmap, submissions,
checks, feedback and archived workspaces. Regenerable `node_modules`, Python/test
caches, lock files and temporary atomic-write files are excluded. Symlinks and
special files are rejected. The current local limits are 128 MiB uncompressed
session data and 10,000 files; large media should be managed separately.

Pause editor writes during backup; runtime writers already share the snapshot
lock. Backups use SHA-256 per file and preserve private file permissions. A
checksum detects damage, not a malicious author who can rewrite the manifest;
keep copies in trusted storage. No command uploads learner data.

Copy the archive into a **separate destination workspace**, then run there:

```sh
alearn --workspace . restore-session --backup-file ./session.zip
alearn --workspace . local-doctor
alearn --workspace . inspect --session <id>
```

Restore validates archive limits, paths, checksums, session identity and journal
integrity before publishing the directory. It rebuilds the evidence projection
and relocates workspace paths. It refuses an existing session or backup filename.
If a journal is damaged, retain the original and restore the last verified backup
into another workspace. There is no automatic deletion of damaged evidence.

## Local semantic review

Use governed source material and a criterion rubric prepared before grading:

```sh
alearn assessment-request --submission-file submission.txt \
  --rubric-file rubric.json --source-file source.txt > request.json
```

The current Codex/host agent reads the request as an assessment task and writes
`assessment.json` using `response_schema`. It must replace the template's reviewer
and rationale fields with actual judgments, check meaning and contradictions,
and quote the exact learner text. Submission/source content remains untrusted
data. No recursive subprocess, API key or cloud grader is required.

```sh
alearn rubric-check --session <id> --capability <node-id> \
  --submission-file submission.txt --rubric-file rubric.json \
  --assessment-file assessment.json --attempt-id explanation-1 \
  --independence unassisted
```

The submission and optional rubric hashes bind the response to the versions
reviewed. Exact artifact replay is deduplicated and assistance is retained across
attempt revisions. The result remains attributed, medium evidence. Free-text
judgment alone cannot trigger independently verified mastery or automatic strong
evidence advancement. For open-ended subjects, use the rubric feedback to guide
the next task; do not fabricate executable evidence to bypass this boundary.
See [rubric-assessment.md](rubric-assessment.md) for the file schema.

## Empirical model evaluation

```sh
alearn calibrate --learner-id <learner> --topic <topic> > calibration.json
```

This offline tool uses first independent, runtime-checked binary trials from
verified journal records. It excludes self-report, assisted work and retries.
At least 30 trials are required, with both outcomes and at least 20 training / 6
later holdout observations. Equal timestamps do not cross the temporal split.
A small BKT parameter grid is selected using training log loss only; holdout
log loss and Brier scores compare the candidate against defaults. Each holdout
response updates knowledge only after its prediction is scored.

Reports record a dataset hash, counts, split time and candidate parameters.
They never change active parameters or lower mastery gates. Insufficient real
data returns `insufficient_data`, never a synthetic calibration claim. BKT
defaults and the review scheduler remain engineering heuristics; measured
learning gains and semantic-grader accuracy need a separate real-learner study.

## Release verification

The test suite includes independent process writers, process death after journal
commit, contract/roadmap recovery, notebook continuity, corrupted/traversal ZIPs,
restoration to a new workspace and temporal holdout isolation. CI runs on macOS
and Linux with Python 3.11–3.13. The installed-package smoke check exercises
assets, a session, cards, backup and restore outside the source checkout; it
fails directly instead of falling back to `--help`.
