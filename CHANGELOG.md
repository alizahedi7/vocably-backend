# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Version bumps are derived from [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/):
`fix:` → **PATCH**, `feat:` → **MINOR**, `BREAKING CHANGE` / `!` → **MAJOR**.

## [Unreleased]

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
