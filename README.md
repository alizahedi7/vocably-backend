# Vocably — Backend

Backend for **Vocably**, an AI-powered flashcard vocabulary-learning app. Users build
vocabulary decks, review them with a Leitner spaced-repetition system, and get AI help
looking up word meanings and generating practice stories.

Built with **FastAPI**, **async SQLAlchemy 2.0**, **PostgreSQL**, and **Alembic**, laid out
as a **clean / hexagonal architecture** so business rules stay independent of frameworks,
the database, and external providers.

## Architecture

The code is organised in concentric layers; dependencies only ever point **inward**.

```
app/
├── domain/           # Enterprise rules — entities, enums, repository PORTS, pure SRS logic.
│                     #   No FastAPI, no SQLAlchemy. Knows nothing about the outside world.
├── application/      # Use cases — orchestrates domain objects. Declares PORTS for the
│                     #   things it needs from the outside (AI, SMS/OTP, Google, repos).
├── infrastructure/   # ADAPTERS — SQLAlchemy models & repositories, AI/OTP/Google
│                     #   implementations. Depends on domain/application, never vice-versa.
├── api/              # Delivery — FastAPI routers, Pydantic schemas, dependency injection,
│                     #   exception handlers. The only layer that speaks HTTP.
└── core/             # Cross-cutting — config, DB engine/session, JWT, logging, exceptions.
```

**Why:** the domain and use cases can be unit-tested with zero I/O, and swapping Postgres,
the AI provider, or the SMS gateway means writing a new adapter — no change to business logic.

## Feature surface

- **Auth** — phone/OTP and Google sign-in behind `OTPSender` / `GoogleVerifier` ports (dev
  stubs included), JWT access + refresh tokens.
- **Onboarding / profile** — name, age range, native & app language.
- **Decks** — CRUD, per-deck colour + progress.
- **Words** — CRUD, assigned to a deck, tracked through Leitner boxes 1–5.
- **Study** — due-card queue, grade `again/hard/good/easy`, spaced-repetition scheduling,
  streak + memory-strength stats.
- **AI Studio** — meaning/sense lookup and story generation behind an `AIService` port
  (deterministic stub included; swap in Claude/OpenAI later).

## Quick start

### Option A — Docker (everything, including Postgres)

```bash
cp .env.example .env          # then edit SECRET_KEY
make up                       # builds the image, starts Postgres, runs migrations, serves API
```

### Option B — Local (uv), Postgres via Docker

```bash
cp .env.example .env
make install                  # uv sync --extra dev
docker compose up -d db       # just Postgres
make migrate                  # alembic upgrade head
make seed                     # optional: demo user + decks + words
make run                      # uvicorn with autoreload
```

API docs: <http://localhost:8000/docs> · Health: <http://localhost:8000/health>

## Common tasks

| Command | Description |
| --- | --- |
| `make run` | Run the API with autoreload |
| `make migrate` | Apply Alembic migrations |
| `make makemigration m="..."` | Autogenerate a migration |
| `make test` | Run the test suite |
| `make lint` / `make format` / `make typecheck` | Quality gates |

## Environment

All configuration is env-driven (see [`.env.example`](.env.example)) and loaded through
`pydantic-settings`. `DATABASE_URL` overrides the discrete `POSTGRES_*` values when set.
