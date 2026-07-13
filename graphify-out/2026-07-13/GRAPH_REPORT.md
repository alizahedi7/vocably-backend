# Graph Report - .  (2026-07-11)

## Corpus Check
- Corpus is ~10,096 words - fits in a single context window. You may not need a graph.

## Summary
- 549 nodes · 1400 edges · 33 communities (32 shown, 1 thin omitted)
- Extraction: 88% EXTRACTED · 12% INFERRED · 0% AMBIGUOUS · INFERRED: 161 edges (avg confidence: 0.56)
- Token cost: 0 input · 0 output

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

## God Nodes (most connected - your core abstractions)
1. `LeitnerBox` - 47 edges
2. `User` - 37 edges
3. `WordRepository` - 29 edges
4. `Word` - 27 edges
5. `UserRepository` - 24 edges
6. `Deck` - 22 edges
7. `AuthService` - 21 edges
8. `OtpChallenge` - 20 edges
9. `NotFoundError` - 19 edges
10. `ReviewGrade` - 18 edges

## Surprising Connections (you probably didn't know these)
- `API Service` --implements--> `Vocably Backend`  [INFERRED]
  docker-compose.yml → README.md
- `Postgres db Service` --implements--> `PostgreSQL`  [INFERRED]
  docker-compose.yml → README.md
- `register_exception_handlers()` --indirect_call--> `AppError`  [INFERRED]
  app/api/errors.py → app/core/exceptions.py
- `UserOut` --uses--> `AgeRange`  [INFERRED]
  app/api/v1/schemas/user.py → app/domain/enums.py
- `CompleteOnboardingIn` --uses--> `AgeRange`  [INFERRED]
  app/api/v1/schemas/user.py → app/domain/enums.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Hexagonal Architecture Layer Stack** — readme_domain_layer, readme_application_layer, readme_infrastructure_layer, readme_api_layer, readme_core_layer [EXTRACTED 0.90]
- **Application Outbound Ports** — readme_otpsender_port, readme_googleverifier_port, readme_aiservice_port, readme_application_layer [EXTRACTED 0.85]

## Communities (33 total, 1 thin omitted)

### Community 0 - "Study & Words API"
Cohesion: 0.06
Nodes (66): build_session(), get_overview(), grade_word(), CurrentUser, description, Query, UUID, Study/review endpoints: home overview, session queue, grading. (+58 more)

### Community 1 - "Deck Domain & Persistence"
Cohesion: 0.08
Nodes (29): do_run_migrations(), Alembic environment — async-aware, driven by application settings., run_migrations_online(), Base, Async SQLAlchemy engine, session factory, and the ORM declarative base., Declarative base for all ORM models., Deck, DeckRepository (+21 more)

### Community 2 - "Dependency Injection & Ports"
Cohesion: 0.06
Nodes (42): AIServiceDep, get_ai_studio_service(), get_auth_service(), get_current_user(), get_deck_repository(), get_deck_service(), get_google_verifier(), get_otp_repository() (+34 more)

### Community 3 - "User Service & Domain"
Cohesion: 0.09
Nodes (24): UUID, User profile & settings use cases., UserService, User domain entity — a pure dataclass, independent of persistence., Update the study streak given that the user studied on ``today``.          Same, User, AgeRange, StrEnum (+16 more)

### Community 4 - "AI Studio Feature"
Cohesion: 0.10
Nodes (30): AIStudioServiceDep, get_ai_service(), generate_story(), look_up_meanings(), CurrentUser, AI Studio endpoints: meaning lookup and story generation., Suggest candidate meanings/senses for a word, in the user's native language., Generate a short practice story from the user's mastered words. (+22 more)

### Community 5 - "Auth Service & Config"
Cohesion: 0.09
Nodes (31): Any, AuthResult, Result of a successful sign-in: tokens plus whether the user is new., TokenPair, AuthService, UUID, Authentication use cases: phone/OTP and Google sign-in, plus token refresh., Create and deliver a fresh OTP challenge for ``phone``. (+23 more)

### Community 6 - "Word Domain & SRS Repository"
Cohesion: 0.10
Nodes (17): datetime, Word, ABC, datetime, UUID, Return a ``{box: count}`` map across all of the user's words., Per deck: ``{deck_id: (word_count, summed_box_values)}`` for progress bars., Per deck: ``{deck_id: due_word_count}``. (+9 more)

### Community 7 - "Auth & Users API"
Cohesion: 0.14
Nodes (30): Aggregates all v1 routers under a single APIRouter., Authentication endpoints: phone/OTP, Google, token refresh., Send a one-time passcode to the given phone number., Verify an OTP and sign in (creating the account if it's new)., refresh(), request_otp(), sign_in_with_google(), verify_otp() (+22 more)

### Community 8 - "Deck & Word Services"
Cohesion: 0.13
Nodes (18): Maps domain exceptions to HTTP responses.  The API layer is the only place that, _status_for(), DeckService, UUID, Decks enriched with word/due counts and a progress percentage., UUID, Word (flashcard) use cases with ownership enforcement., WordService (+10 more)

### Community 9 - "OTP Challenge Flow"
Cohesion: 0.13
Nodes (14): OtpChallenge, datetime, OTP challenge entity — a pending phone-verification attempt., OTPChallengeRepository, ABC, Port: persistence contract for OTP challenges., Return the most recent non-consumed challenge for a phone, if any., Mark all outstanding challenges for a phone as consumed. (+6 more)

### Community 10 - "Architecture Docs & Deployment"
Cohesion: 0.12
Nodes (24): API Service, Postgres db Service, AI Studio (Meaning Lookup + Story Generation), AIService Port, Alembic Migrations, API Layer (Delivery), Application Layer, Async SQLAlchemy 2.0 (+16 more)

### Community 11 - "Decks API"
Cohesion: 0.28
Nodes (16): create_deck(), delete_deck(), get_deck(), list_decks(), CurrentUser, UUID, update_deck(), DeckCreateIn (+8 more)

### Community 12 - "App Bootstrap & Leitner"
Cohesion: 0.21
Nodes (14): FastAPI, register_exception_handlers(), configure_logging(), get_logger(), Minimal structured-ish logging setup., interval_for(), timedelta, create_app() (+6 more)

## Knowledge Gaps
- **7 isolated node(s):** `vocably-backend`, `FastAPI`, `Async SQLAlchemy 2.0`, `AI Studio (Meaning Lookup + Story Generation)`, `Decks` (+2 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **1 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `LeitnerBox` connect `Study & Words API` to `Deck Domain & Persistence`, `AI Studio Feature`, `Auth Service & Config`, `Word Domain & SRS Repository`, `Deck & Word Services`, `Decks API`, `App Bootstrap & Leitner`?**
  _High betweenness centrality (0.104) - this node is a cross-community bridge._
- **Why does `User` connect `User Service & Domain` to `Study & Words API`, `Deck Domain & Persistence`, `Dependency Injection & Ports`, `Auth Service & Config`, `Auth & Users API`?**
  _High betweenness centrality (0.065) - this node is a cross-community bridge._
- **Why does `WordRepository` connect `Word Domain & SRS Repository` to `Study & Words API`, `Deck Domain & Persistence`, `Dependency Injection & Ports`, `User Service & Domain`, `AI Studio Feature`, `Deck & Word Services`?**
  _High betweenness centrality (0.042) - this node is a cross-community bridge._
- **Are the 21 inferred relationships involving `LeitnerBox` (e.g. with `BoxCountOut` and `GradeIn`) actually correct?**
  _`LeitnerBox` has 21 INFERRED edges - model-reasoned connections that need verification._
- **Are the 2 inferred relationships involving `User` (e.g. with `AgeRange` and `AuthMethod`) actually correct?**
  _`User` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Are the 6 inferred relationships involving `WordRepository` (e.g. with `AIStudioService` and `DeckService`) actually correct?**
  _`WordRepository` has 6 INFERRED edges - model-reasoned connections that need verification._
- **Are the 5 inferred relationships involving `UserRepository` (e.g. with `AIStudioService` and `AuthService`) actually correct?**
  _`UserRepository` has 5 INFERRED edges - model-reasoned connections that need verification._