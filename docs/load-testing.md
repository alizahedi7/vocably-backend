# Load readiness: audit, fixes, and the load-test playbook

Target: 200–300 concurrent users at launch, 1,000 at peak, on the production
VPS (≈1 GiB RAM, no swap; Postgres, Redis, Caddy, API, Celery worker and beat
on one host).

## 1. Audit findings

Ranked by what each one does to a learner under load.

| # | Severity | Finding | Where | Status |
|---|---|---|---|---|
| 1 | **Critical** | A request held its pooled DB connection, idle in transaction, for the whole AI provider call (2–40 s). The session checks a connection out at `get_current_user` and kept it until the response. About 15 concurrent cold lookups, reader taps or stories emptied the default 5+10 pool. Every other route (reviews, deck lists) then queued for 30 s and returned 500. | `app/api/deps.py` `get_ai_service`, `get_reader_service` | **Fixed**: `ConnectionReleasingAIService` commits before every provider call |
| 2 | **Critical** | The session committed *after* the response was sent: FastAPI's default `yield`-dependency scope exits after `await response(...)`. Under load this showed up as `POST /decks` → 201, then `POST /words` on that deck → 404. A commit that failed (constraint, dropped connection) still told the client "created". | `SessionDep` in `app/api/deps.py` | **Fixed**: `Depends(get_session, scope="function")` |
| 3 | **Critical** | Every client shared one IP for rate limiting. uvicorn trusts `X-Forwarded-For` only from 127.0.0.1, and Caddy is another container, so the per-IP OTP limit (20/hour) was **one bucket for everyone signing up**. Sign-in would start failing at the 21st new user in an hour. The invite join limit is affected the same way. | `deploy/docker-compose.prod.yml` | **Fixed**: `--forwarded-allow-ips='*'`. Safe because the api publishes no port and Caddy overwrites the header. If prod `.env` already sets `FORWARDED_ALLOW_IPS`, this was already working. |
| 4 | High | `/ai/lookup` and `/ai/story` had no per-user limit. Stories are never cached, so a looping client could drain the gateway balance. | `app/api/v1/routers/ai.py` | **Fixed**: 300 lookups and 20 stories per user per hour (Redis, like the reader limits) |
| 5 | High | The DB pool was left at SQLAlchemy defaults (5+10, 30 s timeout). A 30 s wait turns exhaustion into hung requests that clients retry. | `app/core/database.py` | **Fixed**: 10+10, 10 s timeout, 30 min recycle, all configurable |
| 6 | Medium | uvicorn's keep-alive (5 s) was shorter than Caddy's upstream idle timeout (2 min). Caddy could reuse a connection uvicorn had just closed, which shows up as sporadic 502s under load. | compose `api.command` | **Fixed**: `--timeout-keep-alive 130` |
| 7 | Medium | The AI stub answered instantly, so load tests could not show finding 1 or the real AI concurrency. | `factory.raw_ai_provider` | **Fixed**: `AI_STUB_LATENCY_SECONDS` |
| 8 | **High** | **The API runs as one process, which tops out at ~100 req/s.** It is CPU-bound (one core at 80–90%) and queues non-linearly from there. The 300-VU mix is ~100 req/s, so read p95 went to 650 ms–1.1 s. Four workers on the same load gave p95 10 ms. | compose | **Configurable, not yet set**: `WEB_CONCURRENCY` in `.env` (see §4) |
| 9 | Low | Google sign-in opens a new `httpx.AsyncClient` (a TLS handshake) per call. It is async, so it costs latency, not throughput. | `google_id_token_verifier.py` | Left as is |

**Already sound:**

- **Async throughout.** Every route is `async def`, and every outbound call uses `httpx.AsyncClient` with timeouts. There is no `requests` and no `time.sleep` on the request path.
- **No N+1 on the hot paths.** The overview and deck list fold one `tally_by_deck_and_box`, and the roster is batched and SQL-counted in tests.
- **Indexes cover the hot paths.** `word_progress (user_id, due_at)`, `(user_id, deck_id, due_at)` and `(deck_id, user_id)`; `words.deck_id`; `deck_members.user_id`.
- **AI resilience is already in place.** The cache, lexicon and Redis single-flight together give the "50 users, 1 call" behaviour. There is also failover with a deadline, a circuit breaker, one retry, and a 502 on exhaustion.
- **No retry with backoff is added.** Inside a 45 s budget the right move is the *next* gateway, which failover already does, not a third attempt at a stalled one.

**Deliberately not done:**

- **No Gunicorn.** uvicorn 0.52 has a supervised `--workers` mode and `--limit-max-requests-jitter`, so Gunicorn would add a dependency for nothing.
- **No max-requests recycling.** No leak has been measured, and with one worker a recycle would restart the container.
- **No new background queue for stories.** A story is one bounded call, and a queue needs polling UI in two clients.

## 2. Staging environment for the test

**Never run this against production.** It signs in with a fixed OTP code, and production config refuses that.

Use a box the same size as production, with `docker-compose.prod.yml` and these `.env` overrides:

```env
ENVIRONMENT=staging
DEBUG=false                          # defaults true → SQL echo on every query; it alone halves throughput
OTP_FIXED_CODE=123456
OTP_REQUESTS_PER_IP_PER_HOUR=0       # setup signs in 300 accounts from one IP
SHARES_PER_USER_PER_HOUR=0
ACCESS_TOKEN_EXPIRE_MINUTES=120      # the stress profile outlives a 30 min token
AI_PROVIDER=stub                     # sandbox: no spend
AI_STUB_LATENCY_SECONDS=2.5          # ≈ the measured p50 of the real gateways
EXPOSE_DOCS=true
```

Publish one public-domain book, so the reader buckets have text to tap:

```
docker compose -f docker-compose.prod.yml exec -T api \
  python -m app.scripts.ingest_book standard_ebooks <page-url>
```

Then publish it with `PATCH /admin/books/{id}/publish`. Publishing also warms its lemmas, which makes the tap bucket a cache-hit test, as intended.

## 3. Running it

```
k6 run -e BASE_URL=https://staging.example -e PROFILE=baseline loadtest/vocably.js
k6 run -e BASE_URL=https://staging.example -e PROFILE=launch   loadtest/vocably.js
k6 run -e BASE_URL=https://staging.example -e PROFILE=stress -e ACCOUNTS=500 loadtest/vocably.js
```

| Profile | Shape | Pass means |
|---|---|---|
| `baseline` | 50 VUs, 2 min | every threshold green; this is the regression reference |
| `launch` | ramp to 300, hold 10 min | every threshold green; the launch gate |
| `stress` | 300 → 600 → 1000, hold 3 min | expected to break; read *which* limit broke first |
| `ai-live` | 10 VUs, AI only, 2 min | real gateway: set `AI_PROVIDER` back, keep it short; it spends |

**Traffic mix per iteration:**

| Share | Bucket | What it does |
|---|---|---|
| 45% | read | study overview, deck list, study session, Explore, books, chapter, words |
| 30% | write | add word, study round + grade, share deck |
| 15% | reader | reader taps; cache hits on a warmed book |
| 10% | ai | cold `/ai/lookup` (always a miss), paragraph translation |

Each iteration ends with 1–3 s of think time, so 1,000 VUs is roughly 450 requests a second, not 1,000.

`/ai/story` is not in the mix. It needs learned words, so fresh accounts would only measure a fast 422. It goes through the same connection-releasing path as lookup, and finding 1's test covers that path.

**Thresholds** (k6 fails the run if any is missed):

| Bucket | p95 | p99 |
|---|---|---|
| read, reader | < 300 ms | < 800 ms |
| write | < 500 ms | < 1.2 s |
| ai | < `AI_P95_MS` (default 4 s = stub latency + slack) | — |

Overall, errors must stay under 1% and checks must pass for more than 99%.

**Why k6 rather than Locust:**

- **One load box is enough.** k6 is a single Go binary, and one machine drives 1,000 VUs with think time comfortably. Locust's Python workers need a distributed setup for that.
- **Pass/fail is built in.** Thresholds are first-class and exit non-zero, so the launch gate can sit in CI.
- **The traffic is plain HTTP+JSON.** Locust's advantage, driving Python code, buys nothing here.

## 4. Watching the server during a run

Watch the server from a second terminal on the host:

```
docker stats                                   # CPU / memory per container
docker compose -f docker-compose.prod.yml exec db psql -U vocably -c "
  select state, count(*) from pg_stat_activity
  where datname = 'vocably' group by state;"   # 'idle in transaction' should stay ~0
docker compose -f docker-compose.prod.yml exec redis redis-cli --latency
docker compose -f docker-compose.prod.yml logs -f api | grep -iE "timeout|error|502"
```

**Reading the result:**

| Symptom | Bottleneck | Next step |
|---|---|---|
| `idle in transaction` climbs with AI traffic | finding 1 has regressed | something calls a provider outside the wrapped path |
| `QueuePool limit … timed out` in the API logs; DB CPU is low | the pool | raise `DB_POOL_SIZE` (keep `(size+overflow)×workers` < ~80) |
| API container at ~100% of one core while DB and Redis idle | one process | `WEB_CONCURRENCY=2` (needs ≥2 vCPUs and ~120 MiB free per worker) |
| DB CPU high, slow queries | the database | `pg_stat_statements` top 10 by total time |
| Only the `ai` bucket is slow | gateway latency | expected; check that the other buckets stay green, which is the point of finding 1 |
| `free -m` near zero, OOM kills | RAM on this VPS | do not add workers; move to a 2 GiB box first |

**Before release:** run `launch` green twice and `stress` once, then record the saturation point and its cause here.

## 5. Results

Local runs: a 16-core laptop, Postgres 16 in Docker, stub latency 2 s, `DEBUG=false`. They show *relative* behaviour; staging on a production-sized box is still the gate.

| Run | Server | req/s | read p95 | write p95 | ai p95 | errors |
|---|---|---|---|---|---|---|
| baseline, 50 VUs | 1 process | 20 | 13 ms | 20 ms | 2.03 s | 0.08% → 0 after finding 2 |
| 300 VUs, 2 min hold | 1 process | 98 | **1.12 s** | **1.27 s** | 4.1 s | 0.01% |
| 300 VUs, 2 min hold | 4 workers | 113 | 9.6 ms | 13.9 ms | 2.02 s | 0 |
| ramp to 1000 VUs, 2 min hold | 4 workers | 332 | 88 ms | 101 ms | 2.24 s | 0 / 86,842 |

**What the numbers say:**

- **Workers are the launch gate.** The code fixes (findings 1–3) keep the app correct under load: no errors, no pool exhaustion, AI latency isolated. Capacity, though, is CPU, one core per process.
- **Peak load stays within the database's limits.** At 1,000 VUs Postgres held 46 connections, well under `max_connections` 100, so the pool settings have room.
- **Size the box by CPU.** About 100 req/s per core, and ~110 MiB RAM per worker. Launch (300 VUs) needs ≥ 2 workers, and peak (1,000 VUs) about 4, so the current 1 GiB VPS is enough for launch only if it has ≥ 2 vCPUs. For peak, use 4 vCPUs and 2 GiB.
- **Not yet covered locally:** the reader bucket. No book was ingested on the laptop; run it on staging.
