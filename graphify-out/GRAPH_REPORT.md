# Graph Report - vocably-backend  (2026-07-13)

## Corpus Check
- 113 files · ~23,989 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 826 nodes · 1941 edges · 66 communities (46 shown, 20 thin omitted)
- Extraction: 91% EXTRACTED · 9% INFERRED · 0% AMBIGUOUS · INFERRED: 181 edges (avg confidence: 0.56)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `6c9e567b`
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
- test_user_streak.py
- StubAIService

## God Nodes (most connected - your core abstractions)
1. `LeitnerBox` - 47 edges
2. `User` - 37 edges
3. `WordRepository` - 29 edges
4. `Word` - 27 edges
5. `UserRepository` - 24 edges
6. `UserModel` - 24 edges
7. `RecordingOTPSender` - 24 edges
8. `AuthService` - 22 edges
9. `Deck` - 22 edges
10. `OtpChallenge` - 20 edges

## Surprising Connections (you probably didn't know these)
- `RecordingOTPSender` --uses--> `OTPSender`  [INFERRED]
  tests/api/conftest.py → app/application/ports/otp_sender.py
- `RecordingOTPSender` --uses--> `Base`  [INFERRED]
  tests/api/conftest.py → app/core/database.py
- `RecordingOTPSender` --uses--> `AuthMethod`  [INFERRED]
  tests/api/conftest.py → app/domain/enums.py
- `test_kavenegar_sender_is_selected_when_configured()` --indirect_call--> `KavenegarOTPSender`  [INFERRED]
  tests/unit/test_deps_wiring.py → app/infrastructure/auth/kavenegar_otp_sender.py
- `_FailingOTPSender` --uses--> `OTPSender`  [INFERRED]
  tests/api/test_app.py → app/application/ports/otp_sender.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Hexagonal Architecture Layer Stack** — readme_domain_layer, readme_application_layer, readme_infrastructure_layer, readme_api_layer, readme_core_layer [EXTRACTED 0.90]
- **Application Outbound Ports** — readme_otpsender_port, readme_googleverifier_port, readme_aiservice_port, readme_application_layer [EXTRACTED 0.85]

## Communities (66 total, 20 thin omitted)

### Community 0 - "Study & Words API"
Cohesion: 0.06
Nodes (66): build_session(), get_overview(), grade_word(), CurrentUser, description, ge, le, Query (+58 more)

### Community 1 - "Deck Domain & Persistence"
Cohesion: 0.09
Nodes (19): DeckService, UUID, Deck use cases with ownership enforcement., Decks enriched with word/due counts and a progress percentage., Apply a review grade to a card and advance the user's streak., UUID, Word (flashcard) use cases with ownership enforcement., WordService (+11 more)

### Community 2 - "Dependency Injection & Ports"
Cohesion: 0.10
Nodes (34): get_google_verifier(), get_otp_sender(), _google_id_token_verifier(), GoogleIdentity, GoogleVerifier, ABC, Port: verification of a Google OAuth/OIDC id_token., Verified identity extracted from a Google id_token. (+26 more)

### Community 3 - "User Service & Domain"
Cohesion: 0.11
Nodes (14): UUID, User profile & settings use cases., UserService, User domain entity — a pure dataclass, independent of persistence., User, ABC, UUID, Port: persistence contract for :class:`~app.domain.entities.user.User`. (+6 more)

### Community 4 - "AI Studio Feature"
Cohesion: 0.21
Nodes (17): AIStudioServiceDep, generate_story(), look_up_meanings(), CurrentUser, AI Studio endpoints: meaning lookup and story generation., Suggest candidate meanings/senses for a word, in the user's native language., Generate a short practice story from the user's mastered words., LookupIn (+9 more)

### Community 5 - "Auth Service & Config"
Cohesion: 0.11
Nodes (34): AuthResult, Result of a successful sign-in: tokens plus whether the user is new., TokenPair, AuthService, UUID, Authentication use cases: phone/OTP and Google sign-in, plus token refresh., Create and deliver a fresh OTP challenge for ``phone``., Verify an OTP; sign the user in, creating the account if new. (+26 more)

### Community 6 - "Word Domain & SRS Repository"
Cohesion: 0.10
Nodes (17): datetime, Word, ABC, datetime, UUID, Return a ``{box: count}`` map across all of the user's words., Per deck: ``{deck_id: (word_count, summed_box_values)}`` for progress bars., Per deck: ``{deck_id: due_word_count}``. (+9 more)

### Community 7 - "Auth & Users API"
Cohesion: 0.11
Nodes (38): enforce_otp_request_ip_limit(), Cap OTP requests per client IP so one caller can't drain the SMS budget.      Us, Aggregates all v1 routers under a single APIRouter., SessionDep, Authentication endpoints: phone/OTP, Google, token refresh., Send a one-time passcode to the given phone number., Verify an OTP and sign in (creating the account if it's new)., refresh() (+30 more)

### Community 8 - "Deck & Word Services"
Cohesion: 0.13
Nodes (22): AIServiceDep, get_ai_studio_service(), get_auth_service(), get_current_user(), get_deck_repository(), get_deck_service(), get_study_service(), get_user_repository() (+14 more)

### Community 9 - "OTP Challenge Flow"
Cohesion: 0.18
Nodes (10): AIService, ABC, Port: AI capabilities used by the AI Studio use cases.  The application depends, Return candidate senses for ``term``, explained in ``native_language``., Write a short story that naturally uses the supplied ``words``., AIStudioService, UUID, AI Studio use cases: meaning lookup and story generation.  Business rules live h (+2 more)

### Community 10 - "Architecture Docs & Deployment"
Cohesion: 0.15
Nodes (12): API Service, Postgres db Service, Alembic Migrations, Architecture, Common tasks, Environment, Feature surface, Option A — Docker (everything, including Postgres) (+4 more)

### Community 11 - "Decks API"
Cohesion: 0.28
Nodes (16): create_deck(), delete_deck(), get_deck(), list_decks(), CurrentUser, UUID, update_deck(), DeckCreateIn (+8 more)

### Community 12 - "App Bootstrap & Leitner"
Cohesion: 0.06
Nodes (63): get_session(), AsyncSession, FastAPI dependency yielding a transactional session.      Commits on success, ro, async_sessionmaker, AsyncEngine, auth_headers(), bearer(), client() (+55 more)

### Community 33 - "Audit Playbook"
Cohesion: 0.05
Nodes (33): 1. Correctness / Bugs, 2. Security, 3. Performance, 4. Test Coverage, 5. Tech Debt & Architecture, 6. Dependencies & Migrations, 7. DX & Tooling, 8. Docs (+25 more)

### Community 34 - "Base"
Cohesion: 0.06
Nodes (46): do_run_migrations(), Alembic environment — async-aware, driven by application settings., run_migrations_online(), get_otp_repository(), Base, Async SQLAlchemy engine, session factory, and the ORM declarative base., Declarative base for all ORM models., OtpChallenge (+38 more)

### Community 35 - "RecordingOTPSender"
Cohesion: 0.17
Nodes (24): Captures sent codes instead of delivering them., RecordingOTPSender, AsyncClient, MonkeyPatch, OTP sign-in flow: request a code, verify it, and use the issued tokens., request_code(), test_code_is_locked_after_max_wrong_attempts(), test_code_is_single_use() (+16 more)

### Community 36 - "test_kavenegar_sender.py"
Cohesion: 0.05
Nodes (50): FastAPI, Maps domain exceptions to HTTP responses.  The API layer is the only place that, register_exception_handlers(), _status_for(), OTPSender, ABC, Port: outbound delivery of one-time passcodes (SMS, etc.)., Deliver ``code`` to ``phone``. Raise on unrecoverable delivery failure. (+42 more)

### Community 37 - "[Unreleased]"
Cohesion: 0.22
Nodes (8): [0.1.0] - 2026-07-12, Added, Added, Changed, Changelog, Fixed, Security, [Unreleased]

### Community 38 - "test_study.py"
Cohesion: 0.18
Nodes (11): In-process sliding-window rate limiting.  State lives in process memory: with mu, Allow at most N events per key within a rolling window.      The per-event cap i, Record one event for ``key`` and report whether it fit the budget., SlidingWindowRateLimiter, FakeClock, Sliding-window rate limiter used for OTP abuse protection., test_allows_up_to_the_budget_then_blocks(), test_budget_frees_up_as_the_window_slides() (+3 more)

### Community 39 - "Commit conventions"
Cohesion: 0.33
Nodes (5): Commit conventions, Examples, graphify, Message format, Splitting changes into commits (atomic commits)

### Community 40 - "test_ai.py"
Cohesion: 0.36
Nodes (8): AsyncClient, AI lookup endpoint (through the deterministic stub provider)., test_lookup_requires_authentication(), test_lookup_returns_multiple_senses(), test_lookup_unknown_word_falls_back_to_generic_sense(), test_story_requires_authentication(), test_story_requires_enough_learned_words(), test_story_uses_learned_words()

### Community 41 - "test_auth_google.py"
Cohesion: 0.50
Nodes (4): AsyncClient, Google sign-in (through the dev stub verifier, which trusts sub:email:name token, test_google_sign_in_creates_user(), test_google_sign_in_reuses_existing_account()

### Community 64 - "test_user_streak.py"
Cohesion: 0.31
Nodes (7): Update the study streak given that the user studied on ``today``.          Same, date, Unit tests for the study-streak rules on the User entity., test_consecutive_days_increment_streak(), test_first_ever_study_starts_at_one(), test_gap_resets_streak_to_one(), test_same_day_is_noop()

### Community 65 - "StubAIService"
Cohesion: 0.38
Nodes (3): get_ai_service(), A fake AI provider useful for local dev and tests., StubAIService

## Knowledge Gaps
- **63 isolated node(s):** `vocably-backend`, `Hard Rules`, `Phase 1 — Recon (always)`, `Phase 2 — Audit (parallel)`, `Phase 3 — Vet, prioritize, confirm` (+58 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **20 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `LeitnerBox` connect `Study & Words API` to `Deck Domain & Persistence`, `Base`, `Auth Service & Config`, `Word Domain & SRS Repository`, `OTP Challenge Flow`, `Decks API`?**
  _High betweenness centrality (0.056) - this node is a cross-community bridge._
- **Why does `User` connect `User Service & Domain` to `Study & Words API`, `test_user_streak.py`, `Deck Domain & Persistence`, `Base`, `Auth Service & Config`, `Auth & Users API`, `Deck & Word Services`?**
  _High betweenness centrality (0.039) - this node is a cross-community bridge._
- **Why does `SlidingWindowRateLimiter` connect `test_study.py` to `Deck & Word Services`?**
  _High betweenness centrality (0.030) - this node is a cross-community bridge._
- **Are the 21 inferred relationships involving `LeitnerBox` (e.g. with `BoxCountOut` and `GradeIn`) actually correct?**
  _`LeitnerBox` has 21 INFERRED edges - model-reasoned connections that need verification._
- **Are the 2 inferred relationships involving `User` (e.g. with `AgeRange` and `AuthMethod`) actually correct?**
  _`User` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Are the 6 inferred relationships involving `WordRepository` (e.g. with `AIStudioService` and `DeckService`) actually correct?**
  _`WordRepository` has 6 INFERRED edges - model-reasoned connections that need verification._
- **Are the 5 inferred relationships involving `UserRepository` (e.g. with `AIStudioService` and `AuthService`) actually correct?**
  _`UserRepository` has 5 INFERRED edges - model-reasoned connections that need verification._