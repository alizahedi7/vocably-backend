---
name: run-vocably-backend
description: Run, start, launch, or smoke-test the Vocably backend API — boots Postgres in Docker, applies Alembic migrations, starts uvicorn, and drives a full user flow (OTP login, decks, words, study grading, AI lookup/story) via the committed smoke driver.
---

# Run vocably-backend

FastAPI + async SQLAlchemy + Postgres API. Driven entirely with `curl` — there is no UI.
All paths below are relative to the repo root. The primary tool is the smoke driver at
`.claude/skills/run-vocably-backend/smoke.sh`.

## Prerequisites

Already on this machine: `uv`, `docker`, `jq`, `curl`. A `.env` exists at the repo root
(copied from `.env.example`) and **must** contain `OTP_FIXED_CODE=123456` — the driver's
login depends on it.

## Run (agent path) — the smoke driver

```bash
bash .claude/skills/run-vocably-backend/smoke.sh          # full lifecycle, stops the API at the end
KEEP=1 bash .claude/skills/run-vocably-backend/smoke.sh   # leave the API running on :8010 for manual poking
```

It boots a dedicated `postgres:16-alpine` container `vocably-dev-db` on **localhost:5433**
(NOT 5432 — see Gotchas), runs `alembic upgrade head`, starts uvicorn on **:8010**, then
drives: health → OTP request/verify (fresh random phone, code `123456`) → create deck →
create 3 words → study session → grade each word 4× `easy` to box 5 → AI lookup → AI story
→ study overview. Ends with `SMOKE PASS` or `SMOKE FAIL: <step>` + server log tail.
Takes ~15 s. The db container is left running between runs (cheap, speeds up the next run);
`docker rm -f vocably-dev-db` to tear down.

Manual curl against a kept-alive server: sign in and use the token —

```bash
B=http://127.0.0.1:8010/api/v1
curl -s -X POST $B/auth/otp/request -H 'Content-Type: application/json' -d '{"phone":"+989121234567"}'
TOK=$(curl -s -X POST $B/auth/otp/verify -H 'Content-Type: application/json' \
  -d '{"phone":"+989121234567","code":"123456"}' | jq -r .tokens.access_token)
curl -s $B/users/me -H "Authorization: Bearer $TOK"
```

Endpoint list: `curl -s http://127.0.0.1:8010/openapi.json | jq -r '.paths | keys[]'`,
or browse http://127.0.0.1:8010/docs.

## Direct invocation (no server)

Most PRs here touch services/repos, which the test suite covers without Postgres or the
server (in-memory, isolated from `.env`):

```bash
make test        # uv run pytest -q — 111 tests, ~2 s
make lint
make typecheck
```

## Run (human path)

`make run` starts uvicorn with autoreload on 0.0.0.0:8000 against the `.env` database
config (localhost:5432) — which on this machine is the system Postgres with the wrong
credentials. To use the docker db, export
`DATABASE_URL=postgresql+asyncpg://vocably:vocably@localhost:5433/vocably` first.
`make up` (full docker compose) fails here — see Gotchas.

## Gotchas

- **Host port 5432 is owned by a system PostgreSQL** with non-vocably credentials, so
  `docker compose up -d db` fails with "address already in use" and the README's Option B
  connects to the wrong server. The driver sidesteps both with its own container on 5433
  plus `DATABASE_URL` (env var overrides `.env` via pydantic-settings).
- **Blank `PYTHONPATH`** before any `uv run` — a sourced ROS environment leaks packages
  into uv's venv and breaks pytest plugin autoload. The Makefile and driver both do this.
- **Word/AI schemas use `term`, not `text`** (`WordCreateIn`: `deck_id`, `term`,
  `meaning` required). Sending `text` gets a 422.
- **`/ai/story` requires ≥3 words in Leitner box 4–5** ("learned"). Fresh words start in
  box 1; one `easy` grade moves up one box, so 4× `easy` per word gets you there. Until
  then: `{"error":{"code":"validation_error","message":"Learn at least 3 words..."}}`.
- **Two error envelopes**: domain errors return `{"error":{"code":...,"message":...}}`;
  FastAPI validation errors return `{"detail":[...]}`. Don't parse for just one.
- **Grading works on non-due words** — no need to time-travel `due_at` for a smoke flow.
- **AI is a deterministic stub** (`AI_PROVIDER=stub`), OTP logs to console
  (`OTP_SENDER=console`) — no external calls, no keys needed.
- **Don't `pkill -f uvicorn`** to stop the server — this box runs an unrelated
  `rsdashboard-backend` gunicorn/uvicorn on :8000. Use the pid the driver prints, or
  `pkill -f 'uvicorn app[.]main:app'` (bracket trick also stops the pattern from matching
  your own shell and killing it, exit 144).

## Troubleshooting

- `bind: address already in use` on 5432 → system Postgres; use the driver (5433).
- `password authentication failed for user "vocably"` → you're talking to the system
  Postgres on 5432, not the docker db on 5433; set `DATABASE_URL` as above.
- `SMOKE FAIL: otp verify` → `.env` is missing `OTP_FIXED_CODE=123456`, or
  `ENVIRONMENT=production` (fixed code is rejected there by a Settings validator).
- 422 `Field required: term` → you sent `text`; the field is `term`.
