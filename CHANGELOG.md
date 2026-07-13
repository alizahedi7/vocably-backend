# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Version bumps are derived from [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/):
`fix:` → **PATCH**, `feat:` → **MINOR**, `BREAKING CHANGE` / `!` → **MAJOR**.

## [Unreleased]

### Added

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
