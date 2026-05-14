# robomp

A self-hosted GitHub triage/fix bot that drives `omp --mode rpc`. For each
issue on an allowlisted repo, robomp will:

1. Reply in-thread acknowledging the issue.
2. Reproduce the bug in an isolated workspace.
3. Comment with the reproduction outcome.
4. Implement a fix on a fresh branch.
5. Open a PR with a structured `Repro / Cause / Fix / Verification` body that
   closes the issue (`Fixes #N`).
6. Reply to follow-up comments and PR review comments in the same session.

The orchestrator runs in Docker on a single developer machine. There is no
multi-tenant story; tunnel-from-the-internet plumbing (smee.io / ngrok /
cloudflared) is the operator's responsibility — see [Webhook
tunneling](#webhook-tunneling).

## Architecture

```
                ┌──────────────────────────────────────────────┐
                │ Docker container: robomp                     │
                │                                              │
 GitHub ──webhook──▶ FastAPI receiver (server.py)              │
                │      │                                       │
                │      ▼                                       │
                │   db.py (sqlite) ── deduped event log        │
                │      │                                       │
                │      ▼                                       │
                │   queue.py (asyncio task pool)               │
                │      │                                       │
                │      ▼                                       │
                │   worker.py per task                         │
                │   ├─ sandbox.py: clone pool + git worktree   │
                │   ├─ RpcClient(omp --mode rpc, cwd=clone)    │
                │   ├─ set_todos([Repro, Diagnose, Fix, PR])   │
                │   ├─ set_custom_tools([gh_*, repro_record])  │
                │   ├─ install_headless_ui()                   │
                │   └─ prompt_and_wait(kickoff_or_followup)    │
                │                                              │
                │   github_client.py (httpx)                   │
                └──────────────────────────────────────────────┘
   Mounts: /work/pi (omp source), ./data (sqlite + logs + workspaces)
```

The orchestrator container is the isolation boundary; per-issue git worktrees
give per-task filesystem isolation. There is no docker-in-docker.

## Setup

### Prerequisites

- Docker + Docker Compose v2.
- A checkout of `oh-my-pi` available locally (the image mounts it at
  `/work/pi`).
- A GitHub PAT with `repo` scope on the allowlisted repositories. The PAT
  user must be a collaborator (so it can push branches and open PRs).
- A test repository you control. **Never point robomp at a repo you're not
  willing to receive bot-authored PRs on.**

### One-time

```bash
cp .env.example .env
$EDITOR .env                      # fill in GITHUB_TOKEN, webhook secret, etc.

make build                        # rsync pi → .pi-context/ then docker compose build
make up                           # docker compose up -d (foreground logs)
curl -fsS http://localhost:8080/healthz
```

The image builds pi-natives (the Rust N-API addon) inside its own Linux
builder stage so the runtime image carries a Linux-native
`pi_natives.linux-<arch>.node` regardless of your host OS. `make stage`
rsyncs $PI_ROOT into `.pi-context/`, excluding `target/`, `runs/`,
`node_modules/`, and other build artifacts — without that filter the build
context would be tens of gigabytes.

The runtime container mounts the full `$PI_ROOT` read-only at `/work/pi`
and persists state to `./data` (sqlite, logs, per-issue worktrees). Override
`PI_ROOT` in your environment if your pi checkout lives elsewhere.

### Webhook configuration

In the target repository's *Settings → Webhooks*:

- **Payload URL:** `https://<your-tunnel>/webhook/github`
- **Content type:** `application/json`
- **Secret:** the value of `GITHUB_WEBHOOK_SECRET`
- **Events:**
  - Issues
  - Issue comments
  - Pull request reviews / review comments
  - Pull requests

robomp ignores everything else, but it's harmless to deliver more.

## Webhook tunneling

robomp does not ship a tunnel. The webhook receiver listens on
`:8080/webhook/github` inside the container. Pick whichever of these you
prefer:

- [`smee.io`](https://smee.io/) is the easiest. Create a channel, then run
  `smee --url https://smee.io/<token> --target http://localhost:8080/webhook/github`
  on the host.
- `cloudflared tunnel run` mapped to `localhost:8080`.
- `ngrok http 8080`.

In all cases, the **Payload URL** in the GitHub webhook settings points at the
external endpoint; the **Secret** is the same `GITHUB_WEBHOOK_SECRET` value.

## CLI

The container also exposes a `robomp` CLI for manual operation:

```bash
docker compose exec robomp robomp serve            # default
docker compose exec robomp robomp triage octo/widget#1   # fire one issue offline
docker compose exec robomp robomp status           # dump the issues table
docker compose exec robomp robomp replay <delivery>
docker compose exec robomp robomp cleanup <issue-key>
```

`triage` fetches the live issue body and drives the full pipeline as if a
webhook had arrived. This is the workhorse for offline development.

## Verification

```bash
# Unit tests (no network, no GitHub, no omp subprocess).
pytest -x tests/

# Gated end-to-end smoke against a real `omp --mode rpc` subprocess and a
# fake GitHub via httpx.MockTransport. Requires `omp` on PATH.
ROBOMP_INTEGRATION=1 pytest -x tests/test_worker_smoke.py

# Container health.
make build && make up
curl http://localhost:8080/healthz
```

## Operational notes

- **No PR without repro.** The agent is instructed to call `repro_record`
  before claiming a fix. If it cannot reproduce, it calls
  `mark_unable_to_reproduce` and the issue is marked `abandoned`.
- **One PR per issue.** Follow-up comments and reviews push commits to the
  same branch / PR; the agent never opens a second PR for the same issue.
- **Session persistence.** Every issue gets a `session_dir` under its
  workspace so follow-up prompts resume the agent's prior context without
  re-reading the issue from scratch.
- **Cleanup.** When the PR merges or the issue closes, robomp removes the
  workspace and updates the issue state. To force this, run
  `robomp cleanup <issue-key>`.

## Security posture (v1)

- The PAT is the only credential. A fine-grained token scoped to the
  allowlisted repos is the recommended posture.
- robomp refuses to act on repos absent from `ROBOMP_REPO_ALLOWLIST`. Webhook
  signatures are verified with constant-time HMAC.
- The agent has full read/write access to the workspace clone, but cannot
  shell out to `gh` or `git push` directly — the credentialed remote URL is
  injected into the worktree by the orchestrator and only the `gh_*` host
  tools (audited via sqlite) can push or comment.
- The orchestrator listens on `0.0.0.0:8080` by default; combine with a
  tunnel that authenticates inbound requests in any deployment that isn't
  exclusively localhost.

## Troubleshooting

| Symptom | Likely cause / check |
| --- | --- |
| `401 invalid signature` on webhook | `GITHUB_WEBHOOK_SECRET` mismatch with the repo webhook setting. |
| Container exits immediately with `PI_ROOT … missing` | The host's pi checkout isn't mounted at `/work/pi`. Fix `volumes:` in `docker-compose.yml`. |
| `git push` fails with `Authentication required` | The PAT does not have push access, or its `ROBOMP_BOT_LOGIN` is wrong; the credentialed remote URL is `https://<bot_login>:<token>@github.com/...`. |
| Agent loops on the same comment | A non-bot reply triggered the `handle_comment` task; check `/events` and `/issues`. |
| PR opened without the four template sections | The `gh_open_pr` host tool validates body sections — the agent should never bypass it. Inspect the audit log in `tool_calls`. |
