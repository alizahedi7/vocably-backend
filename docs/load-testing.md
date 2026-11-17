# Load readiness: audit, fixes, and the load-test playbook

Target: 200–300 concurrent users at launch, 1,000 at peak, on one production
host running Postgres, Redis, Caddy, the API, and the Celery worker and beat.

## 1. Audit findings

Ranked by what each one does to a learner under load.

| # | Severity | Finding | Where | Status |
|---|---|---|---|---|
| 1 | **Critical** | A request held its pooled DB connection, idle in transaction, for the whole AI provider call (2–40 s). The session checks a connection out at `get_current_user` and kept it until the response. About 15 concurrent cold lookups, reader taps or stories emptied the default 5+10 pool. Every other route (reviews, deck lists) then queued for 30 s and returned 500. | `app/api/deps.py` `get_ai_service`, `get_reader_service` | **Fixed**: `ConnectionReleasingAIService` commits before every provider call |
| 2 | **Critical** | The session committed *after* the response was sent: FastAPI's default `yield`-dependency scope exits after `await response(...)`. Under load this showed up as `POST /decks` → 201, then `POST /words` on that deck → 404. A commit that failed (constraint, dropped connection) still told the client "created". | `SessionDep` in `app/api/deps.py` | **Fixed**: `Depends(get_session, scope="function")` |
| 3 | **Critical** | Every client shared one IP for rate limiting. uvicorn trusts `X-Forwarded-For` only from 127.0.0.1, and Caddy is another container, so the per-IP OTP limit (20/hour) was **one bucket for everyone signing up**. Sign-in would start failing at the 21st new user in an hour. The invite join limit is affected the same way. | `deploy/docker-compose.prod.yml` | **Fixed**: `--forwarded-allow-ips='*'`, in effect only since finding 12. Safe because the api publishes no port and Caddy overwrites the header: a client-sent `X-Forwarded-For` is logged as the client's real address. Verified through the access log. |
| 4 | High | `/ai/lookup` and `/ai/story` had no per-user limit. Stories are never cached, so a looping client could drain the gateway balance. | `app/api/v1/routers/ai.py` | **Fixed**: 300 lookups and 20 stories per user per hour (Redis, like the reader limits) |
| 5 | High | The DB pool was left at SQLAlchemy defaults (5+10, 30 s timeout). A 30 s wait turns exhaustion into hung requests that clients retry. | `app/core/database.py` | **Fixed**: 10+10, 10 s timeout, 30 min recycle, all configurable |
| 6 | Medium | uvicorn's keep-alive (5 s) was shorter than Caddy's upstream idle timeout (2 min). Caddy could reuse a connection uvicorn had just closed, which shows up as sporadic 502s under load. | compose `api.command` | **Fixed**: `--timeout-keep-alive 130`, in effect only since finding 12 |
| 7 | Medium | The AI stub answered instantly, so load tests could not show finding 1 or the real AI concurrency. | `factory.raw_ai_provider` | **Fixed**: `AI_STUB_LATENCY_SECONDS` |
| 8 | **High** | **The API runs as one process, which tops out at ~100 req/s.** It is CPU-bound (one core at 80–90%) and queues non-linearly from there. The 300-VU mix is ~100 req/s, so read p95 went to 650 ms–1.1 s. Four workers on the same load gave p95 10 ms. | compose | **Configurable**: `WEB_CONCURRENCY` in `.env`, set per host (see §5) |
| 9 | Low | Google sign-in opens a new `httpx.AsyncClient` (a TLS handshake) per call. It is async, so it costs latency, not throughput. | `google_id_token_verifier.py` | Left as is |
| 10 | **High** | A gateway account on a low tier rate-limits at ~10 concurrent AI users: 152 of ~190 calls in two minutes came back `429`. Failover hid it from learners (every request 200), but each refused call costs a wasted round trip first (worst cases 11–19 s), and a chain whose fallback refuses most traffic has one working gateway, not two. | gateway accounts | **Mitigated**: put the gateway that measured clean first in `AI_PROVIDER`; raise the other account's tier before relying on it as the fallback |
| 11 | Medium | uvicorn's access log was silenced everywhere, so a failed request under load could not be traced to a route or a client address. | `app/core/logging.py` | **Fixed**: `ACCESS_LOG`, on in the production compose file, with the api container's log capped at 5 × 20 MB |
| 12 | **Critical** | The api's compose command spread across lines indented deeper than its first, which a YAML `>` block keeps as newlines. `sh -c` ran `uvicorn … --port 8000` alone, so findings 3 and 6 were deployed but never applied. Found by the access log in finding 11, which logged the proxy's address for a request sent through the public URL. | `deploy/docker-compose.prod.yml` | **Fixed**: one indentation; `tests/unit/test_prod_compose.py` fails if any production command parses to more than one line |
| 13 | Low | The `ai-live` profile could not start: `constant-vus` refuses `gracefulRampDown`, which the script set on every scenario. | `loadtest/vocably.js` | **Fixed**; the profile also takes `AI_VUS` |

**Already sound:**

- **Async throughout.** Every route is `async def`, and every outbound call uses `httpx.AsyncClient` with timeouts. There is no `requests` and no `time.sleep` on the request path.
- **No N+1 on the hot paths.** The overview and deck list fold one `tally_by_deck_and_box`, and the roster is batched and SQL-counted in tests.
- **Indexes cover the hot paths.** `word_progress (user_id, due_at)`, `(user_id, deck_id, due_at)` and `(deck_id, user_id)`; `words.deck_id`; `deck_members.user_id`.
- **AI resilience is already in place.** The cache, lexicon and Redis single-flight together give the "50 users, 1 call" behaviour. There is also failover with a deadline, a circuit breaker, one retry, and a 502 on exhaustion.
- **No retry with backoff is added.** Inside a 45 s budget the right move is the *next* gateway, which failover already does, not a third attempt at a stalled one.

**Lesson from finding 12:** a flag in a deploy file is not verified until the running process shows it. `docker compose exec api sh -c 'tr "\0" " " < /proc/10/cmdline'` prints what uvicorn actually received.

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
| `ai-live` | `AI_VUS` (default 10), AI only, 2 min | real gateway: unset the stub, keep it short; it spends. No `429`s in the API log, and no failovers if you are testing one gateway |

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
| read | < 300 ms | < 800 ms |
| reader | median < 300 ms; p95 < `AI_P95_MS` | — |
| write | < 500 ms | < 1.2 s |
| ai | < `AI_P95_MS` (default 4 s = stub latency + slack) | — |

The reader bucket is judged differently because a tap is a cache read *except* the first tap on a sentence, which asks the model what the word means there.

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
docker compose -f docker-compose.prod.yml logs api | grep -E '" 5[0-9]{2} '      # failed requests, by route
docker compose -f docker-compose.prod.yml logs api | grep -iE "429|falling over"  # gateway refusing calls
```

**Reading the result:**

| Symptom | Bottleneck | Next step |
|---|---|---|
| `idle in transaction` climbs with AI traffic | finding 1 has regressed | something calls a provider outside the wrapped path |
| `QueuePool limit … timed out` in the API logs; DB CPU is low | the pool | raise `DB_POOL_SIZE` (keep `(size+overflow)×workers` < ~80) |
| API container at ~100% of one core while DB and Redis idle | one process | `WEB_CONCURRENCY=2` (needs ≥2 vCPUs and ~120 MiB free per worker) |
| DB CPU high, slow queries | the database | `pg_stat_statements` top 10 by total time |
| Only the `ai` bucket is slow | gateway latency | expected; check that the other buckets stay green, which is the point of finding 1 |
| `free -m` near zero, OOM kills | RAM | do not add workers; add memory first |

**Before release:** run `launch` green twice and `stress` once, then record the saturation point and its cause here.

## 5. Results

Local runs: a 16-core laptop, Postgres 16 in Docker, stub latency 2 s, `DEBUG=false`. They show *relative* behaviour; staging on a production-sized box is still the gate.

| Run | Server | req/s | read p95 | write p95 | ai p95 | errors |
|---|---|---|---|---|---|---|
| baseline, 50 VUs | 1 process | 20 | 13 ms | 20 ms | 2.03 s | 0.08% → 0 after finding 2 |
| 300 VUs, 2 min hold | 1 process | 98 | **1.12 s** | **1.27 s** | 4.1 s | 0.01% |
| 300 VUs, 2 min hold | 4 workers | 113 | 9.6 ms | 13.9 ms | 2.02 s | 0 |
| ramp to 1000 VUs, 2 min hold | 4 workers | 332 | 88 ms | 101 ms | 2.24 s | 0 / 86,842 |

**Staging-shaped run** (the production compose shape on a laptop, every container pinned to the same two cores, `WEB_CONCURRENCY=2`, stub latency 2.5 s, one warmed book):

| Run | req/s | read p95 | write p95 | reader median / p95 | ai p95 | errors |
|---|---|---|---|---|---|---|
| baseline, 50 VUs | 26 | 15 ms | 26 ms | 9 ms / 2.67 s | 2.53 s | 0 / 4,527 |
| launch, 300 VUs, 10 min hold | 137 | 31 ms | 43 ms | 8 ms / 32 ms | 2.55 s | 2 / 115,756 |

Every threshold passed on the launch run. The API peaked at about 125% of its two cores' 200%, Postgres at 34% of one, and the pool never went past 21 connections with at most 7 idle in transaction. The baseline's reader p95 is the first-tap model call; by the launch run the sampled sentences were memoised.

**Stress run on the same two-core stack** (ramp 300 → 600 → 1,000 VUs):

| VUs (approx.) | req/s | read p50 / p95 | ai p95 | 500s |
|---|---|---|---|---|
| 300 | 190 | 7 ms / 24 ms | 2.5 s | 0 |
| 400 | 240 | 13 ms / 180 ms | 2.8 s | 0 |
| 500 | 270 | 130 ms / 430 ms | 3.2 s | 0 |
| 600 | 300 | 240 ms / 730 ms | 3.5 s | 0 |
| 1,000 | 295 | 1.4 s / 4.3 s | 8 s | ~11 a minute |

Two cores saturate at about 300 req/s. Thresholds hold to roughly 400–450 VUs; past 600 throughput stops rising and latency absorbs the rest. The limit is API CPU (170% of 200%) while Postgres sat under half a core. The only errors were 33 pool timeouts (0.02% of 172,451 requests) at 1,000 VUs: a symptom of the starved event loop holding connections longer, not of a small pool, so raising `DB_POOL_SIZE` would not help. It degrades by slowing down, and recovered as soon as load fell.

**Real gateways (`ai-live`)**, on a staging-shaped stack with the production gateway settings. Cold lookups were random strings, so every one was a provider call; real words may take longer.

| Run | Requests | Cold lookup p50 / p95 / max | Failed | Notes |
|---|---|---|---|---|
| 10 VUs, primary + fallback | 321 | 2.2 s / 4.1 s / 11 s | 0 | the primary returned `429` on 152 calls; the fallback answered all of them (finding 10) |
| 40 VUs, the clean gateway alone | 1,662 | 1.4 s / 2.5 s / 4.0 s | 0 | 1,012 provider calls, ~8 a second, no `429` |

Throughout, Postgres held 11 connections with none idle in transaction: finding 1's fix holds against real gateway latency, not only the stub.

**What the numbers say:**

- **Workers are the launch gate.** The code fixes (findings 1–3) keep the app correct under load: no errors, no pool exhaustion, AI latency isolated. Capacity, though, is CPU, one core per process.
- **Peak load stays within the database's limits.** At 1,000 VUs Postgres held 46 connections, well under `max_connections` 100, so the pool settings have room.
- **Size the box by CPU.** About 100 req/s per core, and ~110 MiB RAM per worker. Launch (300 VUs) needs ≥ 2 workers, and peak (1,000 VUs) about 4, so launch needs ≥ 2 vCPUs, and peak about 4 vCPUs and 2 GiB.
- **AI capacity is set by the gateway account, not this server.** Measure each gateway alone with `ai-live` before putting it in the chain; a failover that succeeds still doubles latency on every call it catches.
- **Still not covered:** a run through Caddy and TLS on a real second host, and a real gateway above 40 VUs.
