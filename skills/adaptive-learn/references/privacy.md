# Privacy and Telemetry Boundary

Default store:
- explicit answer;
- prediction;
- revision;
- hint/support request;
- artifact reference;
- deterministic execution result;
- transfer/review outcome.

Default do not store for learner inference:
- mouse trajectories;
- hover patterns;
- scroll velocity;
- micro-key timing;
- hardware-derived pseudo-attention metrics.

Latency may be logged for system performance but must not be treated as cognitive evidence without independent corroboration.

## Course Studio: what leaves the machine and what is stored

The runtime itself has no network client and calls no model. Two optional pieces change that boundary.

Runners (off by default; `studio runners configure --confirm`): the studio launches the owner's locally
installed `claude -p` or `codex exec`. Sent to the vendor's hosted model (Anthropic or OpenAI, under the
owner's own CLI login and that vendor's terms): the work order JSON (node spec, targets, previous validator
errors) and the validated PUBLIC source text of the node. Private (file/upload) sources are sent only if the
job was created with `studio init --allow-private-sources`. Not sent by the studio: learner answers,
evidence, the ledger, the item vault, tokens, cookies or `ALEARN_*` values. The runner starts in a fresh
scratch directory outside the workspace with an allowlist-built environment (optionally `ANTHROPIC_API_KEY`
for claude; `OPENAI_API_KEY`, `CODEX_HOME` for codex, names only stored). claude gets no tools; codex a
permission profile that reads only that directory and minimal platform paths and writes nothing. Neither is
network-isolated, and the codex profile was verified with `codex sandbox` for codex-cli 0.155 but not with a
live model call: re-check after upgrades. The runner's reply is untrusted data admitted only through the
validators. Provenance (binary hash, version, exit code, duration, prompt and output hashes; reported model
and cost labelled untrusted) is stored in the job and an anchored event; raw stdout/stderr stay in the
scratch directory, which the studio never deletes.

Owner server (`studio serve`): loopback by default, requires `ALEARN_OWNER_TOKEN`, separate from the learner
server, with Host/Origin checks before the token. The browser keeps the token in memory and sessionStorage.
The node preview shows learner-visible content only (visible check names, blank counts, never hidden checks
or reference solutions) and is refused if a string matches the vault. Event and notification texts, the
worker log and the handover are written without learner answers, secrets or raw runner output (a design rule with tests, not a guarantee about
free text an owner types). The runner configure
event and the owner's acceptance statement are stored in the job and the ledger as written by the owner:
do not put personal data in them.

Never deleted: the studio renames superseded jobs, orders, scratch runs and logs into `studio/archive/`.
