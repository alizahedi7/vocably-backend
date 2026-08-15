# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Version bumps are derived from [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/):
`fix:` → **PATCH**, `feat:` → **MINOR**, `BREAKING CHANGE` / `!` → **MAJOR**.

## [Unreleased]

### Security

- **Sharing a deck with one person no longer opens its public invite link.**
  `POST /decks/{id}/share` ended by setting `is_open = True` and returning the
  code, so naming a single student by handle minted a deck-wide bearer
  credential the owner never asked for, was not told about, and could not see
  without opening the share sheet — and the next share silently re-opened a
  link they had deliberately closed. Sharing now returns the code of an
  already-open link and an empty string otherwise; only `POST
  /decks/{id}/invite` opens one. Links opened this way before the fix are left
  open, because a code already given to a class must keep working.
- **OTP brute-force lockout now engages.** Failed verification attempts were
  rolled back with the failed request, so the five-attempt cap never applied
  and codes could be guessed without limit. Attempts are now counted with an
  atomic increment and committed even when verification fails.
- **Per-IP OTP request limit** — one client IP may request at most
  `OTP_REQUESTS_PER_IP_PER_HOUR` codes (default 20/h, `<= 0` disables), so
  rotating phone numbers can no longer drain the SMS budget.
- **Word text fields are bounded** — `meaning` and `example` are capped at
  2000 characters (the columns are unbounded `TEXT`).

### Added

- **`GET /users/search?q=`** — finds people to share a deck with. Prefix match
  on the **handle only** (never the display name), two characters minimum,
  eight results maximum, shortest first so an exact match leads its
  near-misses, and the searcher is never in their own results. A prefix too
  short or malformed answers `200` with an empty list rather than an error.
  Capped per user through Redis at `USER_SEARCHES_PER_USER_PER_HOUR`
  (default 60) — tighter than the availability check, since one call returns a
  page of real handles rather than a yes/no about one guess.
- **`GET /decks/{id}/shares`** — the sender's half of a share: offers of this
  deck that nobody has answered yet, for whoever may invite. Accepted offers
  are memberships and appear on the roster instead; declined ones are deleted,
  and the sender is not told.
- **`POST /decks/{id}/share` accepts `role`** — what accepting will make the
  recipient, carried on the offer and applied when they answer. Optional and
  defaulting to `viewer`, so an older client keeps the behaviour it had;
  `owner` is downgraded to `viewer` rather than refused. Previously every
  person-to-person share granted viewer whatever the sender chose.
- **`words.definition`** — the dictionary definition on the AI Card Magic card
  back is now persisted. Optional on `POST /words` and `PATCH /words/{id}`
  (max 2000 chars) and returned on every word. Omitting it in a `PATCH` leaves
  the stored value alone, so a client that predates the field cannot wipe one;
  sending `""` clears it. Migration `b7c1e93a4d20`.
- **Word list pagination** — `GET /words` accepts `limit` (default 100,
  max 500) and `offset`; ordering gained an id tie-break so page boundaries
  are deterministic.
- **CI pipeline** — lint, format check, mypy, and the suite on SQLite, plus a
  Postgres job that round-trips the full migration chain and re-runs the suite
  against a real server. `TEST_DATABASE_URL` selects the backend locally too.
- 32 new tests: token expiry / deleted-user / wrong-token-type paths, OTP
  expiry and lockout, ownership and cascade edges, `/ai/story`, `/health`,
  error-envelope guarantees, composition-root wiring, and the rate limiter.
- **OTP resend cooldown** — requesting a new code for the same phone within
  `OTP_RESEND_COOLDOWN_SECONDS` (default 30 s, matching the app's resend timer)
  returns `429 rate_limited`; the previously sent code stays valid.
- **E.164 phone validation** — OTP endpoints reject phone numbers that are not
  `+`-prefixed E.164 (8–15 digits).
- **Kavenegar OTP sender** — real SMS delivery via Kavenegar's Verify Lookup API
  (`OTP_SENDER=kavenegar` + `KAVENEGAR_API_KEY` / `KAVENEGAR_OTP_TEMPLATE`).
- **Google id_token verification** — validates tokens against Google's JWKS with
  audience/issuer checks (`GOOGLE_VERIFIER=google` + `GOOGLE_CLIENT_ID`).
- Async API integration test harness (in-memory SQLite + httpx `ASGITransport`)
  covering auth, users, decks, words, study, and AI lookup.

### Changed

- **BREAKING:** `age_range` values now use the app's display strings
  (`Under 13`, `13–17`, …, `65+`, `Prefer not to share`) instead of hyphenated
  codes; adds the two previously missing options. Migration widens
  `users.age_range` and rewrites stored values.

### Fixed

- Datetimes read from SQLite are re-tagged as UTC so timezone-aware comparisons
  behave the same across database backends.

## [0.1.0] - 2026-07-12

### Added

- Clean/hexagonal project layout: `domain` → `application` → `infrastructure` / `api`,
  with cross-cutting concerns in `core`.
- **Auth** — phone/OTP and Google sign-in behind `OTPSender` / `GoogleVerifier` ports
  (dev stubs included), JWT access + refresh tokens.
- **Onboarding / profile** — name, age range, native & app language.
- **Decks** — CRUD with per-deck colour and progress.
- **Words** — CRUD, deck assignment, Leitner boxes 1–5.
- **Study** — due-card queue, `again/hard/good/easy` grading, spaced-repetition
  scheduling, streak and memory-strength stats.
- **AI Studio** — meaning/sense lookup and story generation behind an `AIService`
  port with a deterministic stub.
- PostgreSQL persistence via async SQLAlchemy 2.0 with Alembic migrations
  (initial schema) and a demo-data seed script.
- Unit tests for Leitner scheduling and user streak logic.
- Docker/Compose stack, Makefile targets, and env-driven configuration
  through `pydantic-settings`.

[Unreleased]: https://github.com/alizahedi7/vocably-backend/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/alizahedi7/vocably-backend/releases/tag/v0.1.0
