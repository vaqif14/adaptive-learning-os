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

## Course Studio operations

Design: [course studio](course-studio.md). Replace `<sid>` with the course session id.

```sh
alearn --workspace . studio doctor --session <sid>          # runners, worker, permissions, event chain, ledger, sandbox
alearn --workspace . studio status --session <sid>          # job, drift, per-unit progress, worker heartbeat
alearn --workspace . studio events --session <sid>          # hash-chained events + verification
alearn --workspace . studio notifications --session <sid> --since 0
alearn --workspace . rules-check --session <sid> --scope quick
```

- **Where things live.** `<session>/studio/` holds `jobs/`, `orders/`, `inbox/`, `staging/`,
  `receipts/`, `handover/`, `scratch/`, `archive/`, `events.jsonl`, `notifications.jsonl` and
  `worker/` (`worker.lock`, `heartbeat.json`, `worker.log`, `runner.json`). Directories 0700, files
  0600. Do not edit them by hand; `studio doctor` reports permission, event-chain and ledger problems.
- **Worker.** `studio start --session <sid> --detach` spawns `alearn studio worker` in its own
  session behind a flock singleton. `studio status` shows `worker {alive, pid, beat_at, state}`:
  `alive` means the lock is held and the heartbeat is at most 120 s old; a held lock with an older
  heartbeat reports `stale`. Stop it with SIGTERM (the runner group is killed, its lease released,
  the worker exits cleanly). It exits on done, `needs_learner`, `blocked`, `cancelled` or after the
  idle limit (default 2 h), and backs off from 30 s to 300 s while idle. Without an enabled runner it
  never issues orders (it plans, runs the gate, submits inbox results and otherwise waits for the
  inline host). A start that finds nothing to do returns `exited_early` with exit code 0, which is
  not a failure.
- **Worker log and never-delete rotation.** `studio/worker/worker.log` is rotated at 5 MB by
  renaming it into `studio/archive/worker-logs/`. Scratch rules-check copies keep the five newest
  under `studio/scratch/`; older ones are renamed into `studio/archive/`. A torn final event line is
  moved to `studio/archive/events-torn-N.jsonl`. Nothing is removed; reclaim disk space by moving
  archive files yourself, as a separate human-run step. Runner scratch directories
  (`alearn-run-*`, outside the workspace) are also never removed by the studio.
- **Schedule instead of a worker.** `studio tick --session <sid>` performs one iteration (skipped while a worker
  loop holds the lock); `studio schedule-print --session <sid> --every-minutes 15` prints a launchd plist
  and, for cadences cron expresses evenly, a crontab line. Nothing is installed.
- **Owner server.** `ALEARN_OWNER_TOKEN="$(openssl rand -base64 36)" alearn --workspace . studio serve --session <sid>`
  listens on `127.0.0.1:8778` by default. It is a separate process from `course serve` and refuses to start
  without the token, with a token under 24 characters, with the learner token's value, or on a wildcard host.
  The token is an environment-only bearer secret with no rotation or expiry: restart the server with a new
  value to change it. The detached worker it starts inherits the environment minus the owner and API tokens.
  The browser gets no gate run: `GET /api/studio/handover` is read-only and reports a stale receipt;
  refresh it with `studio handover` or `rules-check` in a terminal.
- **Backup.** `backup-session` skips `studio/scratch` (disposable copies). After a
  `restore-session`, run `rules-check` again rather than trusting a copied receipt: a receipt is bound to the build digest and the ledger head.
- **Crash and drift.** A job whose roadmap, capability, source hash or validator version changed becomes
  `superseded`; run `studio rebase`. An order whose runner binary hash changed is not executed until
  `studio runners configure` is run again.

## Release verification

The test suite includes independent process writers, process death after journal
commit, contract/roadmap recovery, notebook continuity, corrupted/traversal ZIPs,
restoration to a new workspace and temporal holdout isolation. CI runs on macOS
and Linux with Python 3.11–3.13. The installed-package smoke check exercises
assets, a session, cards, backup and restore outside the source checkout; it
fails directly instead of falling back to `--help`.
