# Graph Report - vocably-backend  (2026-07-13)

## Corpus Check
- 108 files · ~21,608 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 751 nodes · 1755 edges · 64 communities (44 shown, 20 thin omitted)
- Extraction: 90% EXTRACTED · 10% INFERRED · 0% AMBIGUOUS · INFERRED: 173 edges (avg confidence: 0.55)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `9befaade`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Study & Words API
- Deck Domain & Persistence
- Dependency Injection & Ports
- User Service & Domain
- AI Studio Feature
- Auth Service & Config
- Word Domain & SRS Repository
- Auth & Users API
- Deck & Word Services
- OTP Challenge Flow
- Architecture Docs & Deployment
- Decks API
- App Bootstrap & Leitner
- Package: vocably-backend
- Audit Playbook
- Base
- RecordingOTPSender
- test_kavenegar_sender.py
- [Unreleased]
- test_study.py
- Commit conventions
- test_ai.py
- test_auth_google.py
- AI Studio (Meaning Lookup + Story Generation)
- AIService Port
- API Layer (Delivery)
- Application Layer
- Async SQLAlchemy 2.0
- Auth (Phone/OTP + Google Sign-in)
- Clean / Hexagonal Architecture
- Core Layer (Cross-cutting)
- Decks
- Domain Layer
- FastAPI
- GoogleVerifier Port
- Infrastructure Layer (Adapters)
- JWT Access + Refresh Tokens
- Leitner Spaced-Repetition System
- OTPSender Port
- pydantic-settings Configuration
- Study (Due-card Queue + Grading)
- Words

## God Nodes (most connected - your core abstractions)
1. `LeitnerBox` - 47 edges
2. `User` - 37 edges
3. `WordRepository` - 29 edges
4. `Word` - 27 edges
5. `UserRepository` - 24 edges
6. `AuthService` - 22 edges
7. `Deck` - 22 edges
8. `RecordingOTPSender` - 22 edges
9. `UserModel` - 21 edges
10. `OtpChallenge` - 20 edges

## Surprising Connections (you probably didn't know these)
- `RecordingOTPSender` --uses--> `OTPSender`  [INFERRED]
  tests/api/conftest.py → app/application/ports/otp_sender.py
- `RecordingOTPSender` --uses--> `Base`  [INFERRED]
  tests/api/conftest.py → app/core/database.py
- `RecordingOTPSender` --uses--> `AuthMethod`  [INFERRED]
  tests/api/conftest.py → app/domain/enums.py
- `_SigningKey` --uses--> `AuthenticationError`  [INFERRED]
  tests/unit/test_google_verifier.py → app/core/exceptions.py
- `test_bad_tokens_are_rejected()` --indirect_call--> `AuthenticationError`  [INFERRED]
  tests/unit/test_google_verifier.py → app/core/exceptions.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Hexagonal Architecture Layer Stack** — readme_domain_layer, readme_application_layer, readme_infrastructure_layer, readme_api_layer, readme_core_layer [EXTRACTED 0.90]
- **Application Outbound Ports** — readme_otpsender_port, readme_googleverifier_port, readme_aiservice_port, readme_application_layer [EXTRACTED 0.85]

## Communities (64 total, 20 thin omitted)

### Community 0 - "Study & Words API"
Cohesion: 0.06
Nodes (70): build_session(), get_overview(), grade_word(), CurrentUser, description, Query, UUID, Study/review endpoints: home overview, session queue, grading. (+62 more)

### Community 1 - "Deck Domain & Persistence"
Cohesion: 0.10
Nodes (15): DeckService, UUID, Decks enriched with word/due counts and a progress percentage., Deck, DeckRepository, ABC, UUID, Port: persistence contract for :class:`~app.domain.entities.deck.Deck`. (+7 more)

### Community 2 - "Dependency Injection & Ports"
Cohesion: 0.07
Nodes (47): AIServiceDep, get_ai_studio_service(), get_auth_service(), get_current_user(), get_deck_repository(), get_deck_service(), get_google_verifier(), get_study_service() (+39 more)

### Community 3 - "User Service & Domain"
Cohesion: 0.09
Nodes (27): UUID, User profile & settings use cases., UserService, User domain entity — a pure dataclass, independent of persistence., Update the study streak given that the user studied on ``today``.          Same, User, AgeRange, StrEnum (+19 more)

### Community 4 - "AI Studio Feature"
Cohesion: 0.10
Nodes (30): AIStudioServiceDep, get_ai_service(), generate_story(), look_up_meanings(), CurrentUser, AI Studio endpoints: meaning lookup and story generation., Suggest candidate meanings/senses for a word, in the user's native language., Generate a short practice story from the user's mastered words. (+22 more)

### Community 5 - "Auth Service & Config"
Cohesion: 0.06
Nodes (53): get_otp_sender(), AuthResult, Result of a successful sign-in: tokens plus whether the user is new., TokenPair, OTPSender, ABC, Port: outbound delivery of one-time passcodes (SMS, etc.)., Deliver ``code`` to ``phone``. Raise on unrecoverable delivery failure. (+45 more)

### Community 6 - "Word Domain & SRS Repository"
Cohesion: 0.10
Nodes (17): datetime, Word, ABC, datetime, UUID, Return a ``{box: count}`` map across all of the user's words., Per deck: ``{deck_id: (word_count, summed_box_values)}`` for progress bars., Per deck: ``{deck_id: due_word_count}``. (+9 more)

### Community 7 - "Auth & Users API"
Cohesion: 0.14
Nodes (30): Aggregates all v1 routers under a single APIRouter., Authentication endpoints: phone/OTP, Google, token refresh., Send a one-time passcode to the given phone number., Verify an OTP and sign in (creating the account if it's new)., refresh(), request_otp(), sign_in_with_google(), verify_otp() (+22 more)

### Community 8 - "Deck & Word Services"
Cohesion: 0.19
Nodes (13): FastAPI, Maps domain exceptions to HTTP responses.  The API layer is the only place that, register_exception_handlers(), _status_for(), UUID, Word (flashcard) use cases with ownership enforcement., WordService, AlreadyExistsError (+5 more)

### Community 9 - "OTP Challenge Flow"
Cohesion: 0.13
Nodes (15): get_otp_repository(), OtpChallenge, datetime, OTP challenge entity — a pending phone-verification attempt., OTPChallengeRepository, ABC, Port: persistence contract for OTP challenges., Return the most recent non-consumed challenge for a phone, if any. (+7 more)

### Community 10 - "Architecture Docs & Deployment"
Cohesion: 0.15
Nodes (12): API Service, Postgres db Service, Alembic Migrations, Architecture, Common tasks, Environment, Feature surface, Option A — Docker (everything, including Postgres) (+4 more)

### Community 11 - "Decks API"
Cohesion: 0.28
Nodes (16): create_deck(), delete_deck(), get_deck(), list_decks(), CurrentUser, UUID, update_deck(), DeckCreateIn (+8 more)

### Community 12 - "App Bootstrap & Leitner"
Cohesion: 0.10
Nodes (37): async_sessionmaker, AsyncEngine, bearer(), client(), engine(), make_user(), Any, AsyncClient (+29 more)

### Community 33 - "Audit Playbook"
Cohesion: 0.05
Nodes (33): 1. Correctness / Bugs, 2. Security, 3. Performance, 4. Test Coverage, 5. Tech Debt & Architecture, 6. Dependencies & Migrations, 7. DX & Tooling, 8. Docs (+25 more)

### Community 34 - "Base"
Cohesion: 0.11
Nodes (22): do_run_migrations(), Alembic environment — async-aware, driven by application settings., run_migrations_online(), Base, get_session(), AsyncSession, Async SQLAlchemy engine, session factory, and the ORM declarative base., Declarative base for all ORM models. (+14 more)

### Community 35 - "RecordingOTPSender"
Cohesion: 0.17
Nodes (21): otp_sender(), Captures sent codes instead of delivering them., RecordingOTPSender, AsyncClient, MonkeyPatch, OTP sign-in flow: request a code, verify it, and use the issued tokens., request_code(), test_code_is_single_use() (+13 more)

### Community 36 - "test_kavenegar_sender.py"
Cohesion: 0.19
Nodes (15): ExternalServiceError, KavenegarOTPSender, Response, OTP sender backed by Kavenegar's Verify Lookup API (Iranian SMS provider).  Uses, Extract ``return.status`` from Kavenegar's response envelope, if present., AsyncBaseTransport, MockTransport, make_sender() (+7 more)

### Community 37 - "[Unreleased]"
Cohesion: 0.25
Nodes (7): [0.1.0] - 2026-07-12, Added, Added, Changed, Changelog, Fixed, [Unreleased]

### Community 38 - "test_study.py"
Cohesion: 0.54
Nodes (7): AsyncClient, Study endpoints: overview stats, due-word sessions, and Leitner grading., seed_deck_with_words(), test_first_grade_of_the_day_advances_streak_once(), test_grading_moves_boxes_and_schedules_reviews(), test_overview_reflects_box_distribution(), test_session_returns_due_words_and_respects_deck_filter()

### Community 39 - "Commit conventions"
Cohesion: 0.33
Nodes (5): Commit conventions, Examples, graphify, Message format, Splitting changes into commits (atomic commits)

### Community 40 - "test_ai.py"
Cohesion: 0.47
Nodes (5): AsyncClient, AI lookup endpoint (through the deterministic stub provider)., test_lookup_requires_authentication(), test_lookup_returns_multiple_senses(), test_lookup_unknown_word_falls_back_to_generic_sense()

### Community 41 - "test_auth_google.py"
Cohesion: 0.50
Nodes (4): AsyncClient, Google sign-in (through the dev stub verifier, which trusts sub:email:name token, test_google_sign_in_creates_user(), test_google_sign_in_reuses_existing_account()

## Knowledge Gaps
- **62 isolated node(s):** `vocably-backend`, `Hard Rules`, `Phase 1 — Recon (always)`, `Phase 2 — Audit (parallel)`, `Phase 3 — Vet, prioritize, confirm` (+57 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **20 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `LeitnerBox` connect `Study & Words API` to `Deck Domain & Persistence`, `User Service & Domain`, `AI Studio Feature`, `Auth Service & Config`, `Word Domain & SRS Repository`, `Decks API`?**
  _High betweenness centrality (0.062) - this node is a cross-community bridge._
- **Why does `User` connect `User Service & Domain` to `Study & Words API`, `Deck Domain & Persistence`, `Dependency Injection & Ports`, `Auth Service & Config`, `Auth & Users API`?**
  _High betweenness centrality (0.043) - this node is a cross-community bridge._
- **Why does `RecordingOTPSender` connect `RecordingOTPSender` to `Base`, `App Bootstrap & Leitner`, `Auth Service & Config`, `Auth & Users API`?**
  _High betweenness centrality (0.030) - this node is a cross-community bridge._
- **Are the 21 inferred relationships involving `LeitnerBox` (e.g. with `BoxCountOut` and `GradeIn`) actually correct?**
  _`LeitnerBox` has 21 INFERRED edges - model-reasoned connections that need verification._
- **Are the 2 inferred relationships involving `User` (e.g. with `AgeRange` and `AuthMethod`) actually correct?**
  _`User` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Are the 6 inferred relationships involving `WordRepository` (e.g. with `AIStudioService` and `DeckService`) actually correct?**
  _`WordRepository` has 6 INFERRED edges - model-reasoned connections that need verification._
- **Are the 5 inferred relationships involving `UserRepository` (e.g. with `AIStudioService` and `AuthService`) actually correct?**
  _`UserRepository` has 5 INFERRED edges - model-reasoned connections that need verification._