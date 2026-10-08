# Deploy — IT Innovations · Adaptive Learning

Hosts the Adaptive Learning OS server (stdlib HTTP API + split-screen workspace UI)
as a standalone project behind Caddy with automatic HTTPS. Zero runtime
dependencies in the app image.

## 1. DNS
Point a host at the server:

```
adaptive.itinnovations.az   A   <server-ip>
```

A subdomain is used on purpose — the app serves at `/` with absolute `/api/...`
paths, so a subdomain avoids path-prefix rewriting. (Path hosting like
`itinnovations.az/adaptive-learning` would also need relative UI paths + a Caddy
`strip_prefix`.)

## 2. Token (required)
Public binds are fail-closed: without a token the server refuses to start.

```bash
cd deploy
cp .env.example .env
# set a long random token:
echo "ADAPTIVE_API_TOKEN=$(openssl rand -base64 36)" > .env
```

## 3. Run
```bash
cd deploy
docker compose up -d --build
```
Caddy obtains a Let's Encrypt certificate automatically (needs ports 80 + 443
open). Then open:

```
https://adaptive.itinnovations.az
```

The UI has a token field (top-right) — paste the `ADAPTIVE_API_TOKEN`. Every
`/api/*` call requires it; `/health` is open for liveness only.

## 4. Operate
- Logs: `docker compose logs -f app`
- Update: `git pull && docker compose up -d --build`
- Data: the append-only ledger + sessions persist in the `learning-data` volume.
  Back it up (`docker run --rm -v deploy_learning-data:/d -v "$PWD":/b alpine tar czf /b/learning-backup.tgz -C /d .`).
- Integrity check (cron): `docker compose exec app alearn --workspace /app ledger-verify --session <id>` (exit 2 if the hash chain is broken).

## Security notes
- **Auth:** token-gated (constant-time compare), fail-closed, plus a Host/Origin
  guard. Intended for a trusted organization audience who hold the token.
- **Code execution:** `/api/run` and `/api/verify-*` execute learner code. The
  base image runs Python locally **inside the app container** — fine for a trusted
  org, NOT safe for arbitrary public untrusted code. For untrusted/multi-user code
  run each submission in an isolated container: set `ADAPTIVE_EXECUTOR=container`
  and give the app access to a Docker socket or a sibling runtime (adds privilege —
  review before enabling). A microVM (Firecracker/gVisor) is the hardened option.
- **Secrets:** keep `deploy/.env` out of git (already gitignored). Rotate the token
  by editing `.env` and `docker compose up -d`.
- Non-loopback without a token is always refused; `--allow-anon` is loopback-only
  and never used in this deploy.

## Scope
This deploy is production-ready for a trusted single-organization audience. A fully
public multi-tenant SaaS additionally needs: per-user auth, container/microVM code
isolation enabled, a language-image matrix, and ledger partitioning/archival at
scale (see the project README "Known limitations").
