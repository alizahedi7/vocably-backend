// Vocably load test — k6 (https://k6.io). Run against STAGING, never production:
// it signs in with a fixed OTP code, which the production config refuses.
//
//   k6 run -e BASE_URL=https://staging.example -e PROFILE=launch loadtest/vocably.js
//
// PROFILE: baseline (50 VUs, 2 min) | launch (300 VUs, 10 min) |
//          stress (ramp to 1000) | ai-live (10 VUs on a REAL gateway, AI only)
// See docs/load-testing.md for the staging env this expects.

import http from 'k6/http';
import { check, sleep } from 'k6';
import exec from 'k6/execution';

const BASE = (__ENV.BASE_URL || 'http://localhost:8000') + '/api/v1';
const PROFILE = __ENV.PROFILE || 'baseline';
const ACCOUNTS = parseInt(__ENV.ACCOUNTS || '300', 10);
const OTP_CODE = __ENV.OTP_CODE || '123456';
// +98 9xx: a range no real subscriber is in on a staging box. Override if needed.
const PHONE_PREFIX = __ENV.PHONE_PREFIX || '+98999';
// Upper bound the AI bucket is judged against: stub latency (or a gateway's p95) + slack.
const AI_P95_MS = parseInt(__ENV.AI_P95_MS || '4000', 10);

const PROFILES = {
  baseline: { executor: 'ramping-vus', stages: [
    { duration: '30s', target: 50 }, { duration: '2m', target: 50 }, { duration: '15s', target: 0 } ] },
  launch: { executor: 'ramping-vus', stages: [
    { duration: '3m', target: 300 }, { duration: '10m', target: 300 }, { duration: '1m', target: 0 } ] },
  stress: { executor: 'ramping-vus', stages: [
    { duration: '2m', target: 300 }, { duration: '3m', target: 600 },
    { duration: '3m', target: 1000 }, { duration: '3m', target: 1000 }, { duration: '1m', target: 0 } ] },
  'ai-live': { executor: 'constant-vus', vus: 10, duration: '2m', env: { AI_ONLY: '1' } },
};

export const options = {
  setupTimeout: '10m',
  scenarios: { [PROFILE]: Object.assign({ gracefulRampDown: '30s' }, PROFILES[PROFILE]) },
  // Per-bucket latency budgets. The stress profile is expected to break these —
  // the point there is to read WHERE it broke, so it does not abort.
  thresholds: {
    http_req_failed: ['rate<0.01'],
    'http_req_duration{kind:read}': ['p(95)<300', 'p(99)<800'],
    'http_req_duration{kind:write}': ['p(95)<500', 'p(99)<1200'],
    // A tap is a cache read, except the first tap on a sentence, which asks the
    // model what the word means there. So: fast at the median, AI-bounded at p95.
    'http_req_duration{kind:reader}': ['med<300', `p(95)<${AI_P95_MS}`],
    'http_req_duration{kind:ai}': [`p(95)<${AI_P95_MS}`],
    checks: ['rate>0.99'],
  },
};

const HOT_WORDS = ['time', 'house', 'eyes', 'night', 'door', 'heart', 'water', 'voice'];

function json(body) { return { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }; }
function authed(token, kind, name) {
  return { headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
           tags: { kind, name } };
}

// ── setup: sign in ACCOUNTS users once, find a published book to read ──────────
export function setup() {
  const tokens = [];
  for (let start = 0; start < ACCOUNTS; start += 25) {
    const phones = [];
    for (let i = start; i < Math.min(start + 25, ACCOUNTS); i++) {
      phones.push(PHONE_PREFIX + String(1000000 + i).slice(-7));
    }
    http.batch(phones.map((phone) => ['POST', `${BASE}/auth/otp/request`, JSON.stringify({ phone }),
      { headers: { 'Content-Type': 'application/json' }, tags: { kind: 'setup' } }]));
    const verified = http.batch(phones.map((phone) => ['POST', `${BASE}/auth/otp/verify`,
      JSON.stringify({ phone, code: OTP_CODE }),
      { headers: { 'Content-Type': 'application/json' }, tags: { kind: 'setup' } }]));
    verified.forEach((r, k) => {
      if (r.status !== 200) throw new Error(`sign-in failed for ${phones[k]}: ${r.status} ${r.body}`);
      tokens.push(r.json('tokens.access_token'));
    });
  }
  // Handles, so decks can be shared between test accounts.
  tokens.forEach((t, i) => http.patch(`${BASE}/users/me`, JSON.stringify({ username: `lt_user_${i}` }),
    authed(t, 'setup', 'set-username')));

  const books = http.get(`${BASE}/books?limit=5`, authed(tokens[0], 'setup', 'books')).json('items') || [];
  let reader = null;
  if (books.length) {
    const detail = http.get(`${BASE}/books/${books[0].id}`, authed(tokens[0], 'setup', 'book')).json();
    const chapter = http.get(`${BASE}/books/${detail.id}/chapters/${detail.chapters[0].id}`,
      authed(tokens[0], 'setup', 'chapter')).json();
    reader = { bookId: detail.id, chapterIds: detail.chapters.slice(0, 5).map((c) => c.id),
               blocks: chapter.blocks.filter((b) => b.kind === 'paragraph' && b.word_count > 15).slice(0, 40) };
  } else {
    console.warn('No published book on this server: reader traffic will be skipped.');
  }
  return { tokens, reader };
}

// ── per-VU state: one deck of its own, created on first use ────────────────────
let myDeck = null;
let myWords = [];

function ensureDeck(token) {
  if (myDeck) return myDeck;
  const r = http.post(`${BASE}/decks`, JSON.stringify({ name: `load ${exec.vu.idInTest}` }),
    authed(token, 'write', 'POST /decks'));
  check(r, { 'deck created': (x) => x.status === 201 });
  myDeck = r.status === 201 ? r.json('id') : null;
  return myDeck;
}

// ── traffic buckets ─────────────────────────────────────────────────────────────
function read(token, data) {
  const pick = Math.random();
  let r;
  if (pick < 0.25) r = http.get(`${BASE}/study/overview`, authed(token, 'read', 'GET /study/overview'));
  else if (pick < 0.45) r = http.get(`${BASE}/decks`, authed(token, 'read', 'GET /decks'));
  else if (pick < 0.6) r = http.get(`${BASE}/study/session`, authed(token, 'read', 'GET /study/session'));
  else if (pick < 0.7) r = http.get(`${BASE}/decks/public`, authed(token, 'read', 'GET /decks/public'));
  else if (pick < 0.8) r = http.get(`${BASE}/books?limit=20`, authed(token, 'read', 'GET /books'));
  else if (pick < 0.9 && data.reader) {
    const c = data.reader.chapterIds[Math.floor(Math.random() * data.reader.chapterIds.length)];
    r = http.get(`${BASE}/books/${data.reader.bookId}/chapters/${c}`, authed(token, 'read', 'GET /books/:id/chapters/:id'));
  } else {
    const deck = ensureDeck(token);
    r = http.get(`${BASE}/words?deck_id=${deck}`, authed(token, 'read', 'GET /words'));
  }
  check(r, { 'read ok': (x) => x.status === 200 });
}

function write(token, data) {
  const deck = ensureDeck(token);
  const pick = Math.random();
  if (pick < 0.45 || myWords.length === 0) {
    const n = `${exec.vu.idInTest}-${exec.vu.iterationInScenario}`;
    const r = http.post(`${BASE}/words`, JSON.stringify({ deck_id: deck, term: `word${n}`, meaning: `meaning ${n}` }),
      authed(token, 'write', 'POST /words'));
    check(r, { 'word created': (x) => x.status === 201 });
    if (r.status === 201) myWords.push(r.json('id'));
  } else if (pick < 0.95) {
    // A study round: fetch the session, grade what it served (same-day practice works).
    const s = http.get(`${BASE}/study/session`, authed(token, 'read', 'GET /study/session'));
    const words = s.status === 200 ? (s.json('words') || []) : [];
    const id = words.length ? words[0].id : myWords[Math.floor(Math.random() * myWords.length)];
    const grades = ['again', 'hard', 'good', 'good', 'easy'];
    const r = http.post(`${BASE}/study/words/${id}/grade`,
      JSON.stringify({ grade: grades[Math.floor(Math.random() * grades.length)], latency_ms: 2500 }),
      authed(token, 'write', 'POST /study/words/:id/grade'));
    check(r, { 'graded': (x) => x.status === 200 });
  } else {
    const to = Math.floor(Math.random() * data.tokens.length);
    const r = http.post(`${BASE}/decks/${deck}/share`, JSON.stringify({ to_username: `lt_user_${to}` }),
      authed(token, 'write', 'POST /decks/:id/share'));
    // 409/422: sharing with yourself or someone already in the deck is a correct refusal.
    check(r, { 'share answered': (x) => [200, 201, 409, 422].includes(x.status) });
  }
}

function tapWord(token, data) {
  if (!data.reader) return;
  const block = data.reader.blocks[Math.floor(Math.random() * data.reader.blocks.length)];
  // Hot words when the block has one (cache hits), else any plain word in it.
  const plain = block.text.match(/\b[A-Za-z]{4,}\b/g) || ['time'];
  const hot = plain.filter((w) => HOT_WORDS.includes(w.toLowerCase()));
  const word = (hot.length ? hot : plain)[Math.floor(Math.random() * (hot.length || plain.length))];
  const start = block.text.indexOf(word); // ASCII prose: UTF-16 index == code point offset
  const r = http.post(`${BASE}/reader/lookup-word`, JSON.stringify({
    word, book_id: data.reader.bookId, block_id: block.id, char_start: start, char_end: start + word.length,
  }), authed(token, 'reader', 'POST /reader/lookup-word'));
  check(r, { 'tap ok': (x) => x.status === 200 });
}

function heavyAI(token, data) {
  const pick = Math.random();
  let r;
  if (pick < 0.6 || !data.reader) {
    // A string nobody has looked up: a guaranteed cache miss, i.e. a provider call.
    const term = `zq${exec.vu.idInTest}x${exec.vu.iterationInScenario}${Math.floor(Math.random() * 1e6)}`;
    r = http.post(`${BASE}/ai/lookup`, JSON.stringify({ term }), authed(token, 'ai', 'POST /ai/lookup (cold)'));
  } else {
    const block = data.reader.blocks[Math.floor(Math.random() * data.reader.blocks.length)];
    r = http.post(`${BASE}/reader/translate-paragraph`, JSON.stringify({ block_id: block.id }),
      authed(token, 'ai', 'POST /reader/translate-paragraph'));
  }
  check(r, { 'ai ok': (x) => x.status === 200 });
}

export default function (data) {
  const token = data.tokens[(exec.vu.idInTest - 1) % data.tokens.length];
  if (__ENV.AI_ONLY) {
    heavyAI(token, data);
  } else {
    const p = Math.random();
    if (p < 0.45) read(token, data);
    else if (p < 0.75) write(token, data);
    else if (p < 0.9) tapWord(token, data);
    else heavyAI(token, data);
  }
  sleep(1 + Math.random() * 2); // think time: a VU is a person, not a request loop
}
