#!/usr/bin/env bash
# Launch vocably-backend and drive one real user flow end-to-end:
#   OTP login -> deck -> 3 words -> study session -> grade to mastery -> AI lookup + story.
#
# Usage (from the repo root):
#   bash .claude/skills/run-vocably-backend/smoke.sh          # full lifecycle, stops uvicorn at the end
#   KEEP=1 bash .claude/skills/run-vocably-backend/smoke.sh   # leave the API running for manual poking
#
# Env overrides: PORT (default 8010), DB_PORT (default 5433), DB_CONTAINER (vocably-dev-db)
set -euo pipefail

PORT="${PORT:-8010}"
DB_PORT="${DB_PORT:-5433}"
DB_CONTAINER="${DB_CONTAINER:-vocably-dev-db}"
BASE="http://127.0.0.1:${PORT}"
API="${BASE}/api/v1"
export DATABASE_URL="postgresql+asyncpg://vocably:vocably@localhost:${DB_PORT}/vocably"
# System tools (e.g. ROS) may export PYTHONPATH and break uv's venv isolation.
export PYTHONPATH=

LOG="$(mktemp -t vocably-uvicorn.XXXXXX.log)"
UVICORN_PID=""
fail() { echo "SMOKE FAIL: $*" >&2; [ -f "$LOG" ] && tail -20 "$LOG" >&2; exit 1; }
cleanup() {
  if [ -n "$UVICORN_PID" ] && [ "${KEEP:-0}" != "1" ]; then
    kill "$UVICORN_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

# ── 1. Postgres (dedicated container; host 5432 may belong to a system postgres) ──
if ! docker inspect -f '{{.State.Running}}' "$DB_CONTAINER" 2>/dev/null | grep -q true; then
  docker rm -f "$DB_CONTAINER" >/dev/null 2>&1 || true
  docker run -d --name "$DB_CONTAINER" \
    -e POSTGRES_USER=vocably -e POSTGRES_PASSWORD=vocably -e POSTGRES_DB=vocably \
    -p "127.0.0.1:${DB_PORT}:5432" postgres:16-alpine >/dev/null
fi
for _ in $(seq 1 30); do
  docker exec "$DB_CONTAINER" pg_isready -U vocably -d vocably >/dev/null 2>&1 && break
  sleep 1
done
docker exec "$DB_CONTAINER" pg_isready -U vocably -d vocably >/dev/null || fail "postgres not ready"

# ── 2. Deps + migrations ──
uv sync --extra dev >/dev/null
uv run alembic upgrade head >/dev/null || fail "alembic upgrade failed"

# ── 3. API ──
uv run uvicorn app.main:app --host 127.0.0.1 --port "$PORT" >"$LOG" 2>&1 &
UVICORN_PID=$!
for _ in $(seq 1 30); do
  curl -sf "$BASE/health" >/dev/null 2>&1 && break
  sleep 1
done
curl -sf "$BASE/health" | jq -e '.status == "ok"' >/dev/null || fail "/health not ok"
echo "API up on $BASE (pid $UVICORN_PID, log $LOG)"

# ── 4. Drive the flow ──
PHONE="+9891$(shuf -i 10000000-99999999 -n1)"   # fresh user each run
curl -sf -X POST "$API/auth/otp/request" -H 'Content-Type: application/json' \
  -d "{\"phone\":\"$PHONE\"}" >/dev/null || fail "otp request"
# OTP_FIXED_CODE=123456 in .env makes the code deterministic in dev.
AUTH=$(curl -sf -X POST "$API/auth/otp/verify" -H 'Content-Type: application/json' \
  -d "{\"phone\":\"$PHONE\",\"code\":\"123456\"}") || fail "otp verify (is OTP_FIXED_CODE=123456 in .env?)"
TOK=$(echo "$AUTH" | jq -re .tokens.access_token) || fail "no access token"
A="Authorization: Bearer $TOK"
echo "signed in as $PHONE"

DECK=$(curl -sf -X POST "$API/decks" -H "$A" -H 'Content-Type: application/json' \
  -d '{"name":"Smoke Deck"}' | jq -re .id) || fail "create deck"

WORDS=()
for TERM in serendipity ephemeral quixotic; do
  W=$(curl -sf -X POST "$API/words" -H "$A" -H 'Content-Type: application/json' \
    -d "{\"deck_id\":\"$DECK\",\"term\":\"$TERM\",\"meaning\":\"meaning of $TERM\"}" | jq -re .id) \
    || fail "create word $TERM"
  WORDS+=("$W")
done

DUE=$(curl -sf "$API/study/session" -H "$A" | jq -re .count) || fail "study session"
[ "$DUE" -ge 3 ] || fail "expected >=3 due words, got $DUE"

# 4x "easy" drives a word from box 1 to box 5 (MASTERED).
for W in "${WORDS[@]}"; do
  for _ in 1 2 3 4; do
    BOX=$(curl -sf -X POST "$API/study/words/$W/grade" -H "$A" \
      -H 'Content-Type: application/json' -d '{"grade":"easy"}' | jq -re .box) || fail "grade $W"
  done
  [ "$BOX" = 5 ] || fail "word $W not mastered (box $BOX)"
done

curl -sf -X POST "$API/ai/lookup" -H "$A" -H 'Content-Type: application/json' \
  -d '{"term":"serendipity"}' | jq -e '.suggestions | length > 0' >/dev/null || fail "ai lookup"

# Story needs >=3 words in box 4-5 — satisfied by the grading above.
STORY=$(curl -sf -X POST "$API/ai/story" -H "$A" -H 'Content-Type: application/json' \
  -d "{\"deck_id\":\"$DECK\"}") || fail "ai story"
echo "$STORY" | jq -e '.words_used | length >= 3' >/dev/null || fail "story missing words"

STREAK=$(curl -sf "$API/study/overview" -H "$A" | jq -re .streak) || fail "overview"

echo "story: $(echo "$STORY" | jq -r .text | head -c 120)..."
echo "streak: $STREAK"
echo "SMOKE PASS"
if [ "${KEEP:-0}" = "1" ]; then
  echo "API left running: $BASE/docs (kill $UVICORN_PID to stop)"
fi
