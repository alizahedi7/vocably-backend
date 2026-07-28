## Commit conventions

All commits MUST follow [Conventional Commits v1.0.0](https://www.conventionalcommits.org/en/v1.0.0/#specification).

### Message format

```
<type>[optional scope][!]: <description>

[optional body]

[optional footer(s)]
```

- **type**: one of
  - `feat` — a new feature (correlates with MINOR in SemVer)
  - `fix` — a bug fix (correlates with PATCH in SemVer)
  - `docs` — documentation-only changes
  - `refactor` — code change that neither fixes a bug nor adds a feature
  - `perf` — performance improvement
  - `test` — adding or correcting tests
  - `build` — build system or dependencies (pyproject.toml, Dockerfile, docker-compose.yml, Makefile)
  - `ci` — CI configuration
  - `chore` — maintenance that touches no app code or tests (e.g. .gitignore, .env.example)
  - `style` — formatting only, no logic change
- **scope** (optional, recommended): the area of the codebase in parentheses, e.g. `feat(auth):`, `fix(api):`, `refactor(models):`, `test(words):`, `build(docker):`. Use short, consistent, lowercase scopes matching module/package names under `app/` (e.g. `auth`, `api`, `models`, `schemas`, `services`, `db`, `alembic`).
- **description**: imperative mood, lowercase, no trailing period, ≤ 72 chars (e.g. "add JWT refresh endpoint", not "Added JWT refresh endpoint.").
- **body** (optional): explain *what* and *why*, not *how*. Separate from the description with a blank line.
- **breaking changes**: append `!` after the type/scope (`feat(api)!: ...`) and/or add a `BREAKING CHANGE: <description>` footer. Either MUST be present for any backward-incompatible change (correlates with MAJOR in SemVer).
- **footers**: `<token>: <value>` format, e.g. `Refs: #123`, `Reviewed-by: ...`.

### Examples

```
feat(words): add spaced-repetition scheduling to review queue
```

```
fix(auth): reject expired refresh tokens in /auth/refresh

Tokens past their expiry were still accepted because the check
compared against issued-at instead of expires-at.

Refs: #42
```

```
feat(api)!: rename /v1/cards endpoints to /v1/words

BREAKING CHANGE: all /v1/cards routes are removed; clients must
migrate to /v1/words.
```

```
build(docker): pin postgres image to 16-alpine
```

```
test(services): cover duplicate-word handling in WordService
```

### Splitting changes into commits (atomic commits)

- **One logical change per commit.** Never bundle unrelated changes into a single commit. If the description needs "and" to connect unrelated things, split it.
- **Group related files together.** A logical change spans all files it requires: e.g. a new endpoint plus its schema, service function, and tests belong in ONE commit; but that endpoint and an unrelated bug fix belong in TWO commits.
- **Migrations travel with their model change**: an alembic migration commits together with the model change that required it.
- **Tests go with the code they test** when written in the same change; standalone test improvements are their own `test:` commit.
- **Formatting/refactoring is separated from behavior changes**: never mix a `refactor`/`style` change with a `feat`/`fix` in the same commit.
- Before committing, review the full working tree (`git status`, `git diff`), plan the sequence of commits, and stage selectively (`git add <paths>` or `git add -p`) so each commit is self-contained, buildable, and reflects exactly one logical change.
- Each commit should leave the project in a working state (tests passing, imports resolving).

## Admin surface

The read-only admin analytics API backing the standalone **vocably-admin** dashboard.

- **Role**: `users.is_admin` (boolean, defaults false). Grant/revoke out of band —
  `make grant-admin who="+989121234567"` (add `revoke=1` to remove). There is no
  self-service path to admin and no endpoint that escalates a user.
- **Gate**: every `/api/v1/admin/*` route depends on `CurrentAdmin` (`require_admin` in
  [deps.py](app/api/deps.py)), which layers on `get_current_user`. Unauthenticated → **401**;
  authenticated non-admin → **403** (the token is valid, the user just lacks access).
  Gating is per-route by design — a new admin route MUST take `CurrentAdmin`, or it is public
  to any signed-in user.
- **Endpoints**: `overview`, `registrations?days=1..365`, `auth-methods`, `users`,
  `categories`, `words`. All GET, all platform-wide, none mutate.
- **Response contract**: schemas in [admin.py](app/api/v1/schemas/admin.py) serialise via
  `serialization_alias` to **camelCase** to match vocably-admin's TypeScript types. Renaming a
  field is a breaking change for that client — keep the aliases in sync with the dashboard.
- **Layering** follows the rest of the app: router → `AdminService` → `AdminRepository` port →
  `SqlAlchemyAdminRepository`. Aggregates are computed in SQL, not by loading rows into Python.
- `users.last_login_at` is stamped on every OTP and Google sign-in (`AuthService`); it powers
  the "Last Login" column and the active-users metric. `NULL` means never signed in since the
  column was added.

## AI surface

`POST /api/v1/ai/lookup` backs the "AI Card Magic" deck; `POST /api/v1/ai/story`
backs practice stories.

- **Provider** is chosen by `AI_PROVIDER`: `stub` (default — deterministic, offline,
  what the tests run against), `anthropic`, or `avalai`. Model, base URL, timeout,
  and max tokens are all env config ([config.py](app/core/config.py)) because the
  right model changes faster than this code does. `ANTHROPIC_BASE_URL` points the
  Anthropic adapter at any gateway speaking the Anthropic protocol; `avalai`
  ([avalai_ai_service.py](app/infrastructure/ai/avalai_ai_service.py)) talks to
  AvalAI's OpenAI-protocol gateway instead, via the `openai` SDK.
- **Never commit a key.** `ANTHROPIC_API_KEY`/`AVALAI_API_KEY` are environment-only;
  startup fails if `AI_PROVIDER` selects a provider without its key (and, for
  `avalai`, without `AVALAI_MODEL` — there is no sane default across an arbitrary
  gateway's model catalogue).
- **The prompt is the product surface.** [prompts.py](app/infrastructure/ai/prompts.py)
  decides tone, dictionary register, and age-appropriateness of everything a learner
  reads on a card. Review changes to it as a product change, not a refactor. Both
  providers share it and the response payload models in
  [payloads.py](app/infrastructure/ai/payloads.py).
- **Guardrails** live in the prompt + `LookupStatus`: a sentence is reduced to its key
  item (`extracted`), a typo corrected (`corrected`), native-language input translated
  (`translated`), and unintelligible input returns *no* senses (`unsupported`) rather
  than an invented definition. Learner text is always wrapped in `<learner_input>` and
  declared to be data, never instruction.
- **Never trust the model's shape.** Responses are schema-constrained (`output_config`
  for Anthropic, `response_format` for AvalAI), then re-validated with Pydantic; one
  retry, then `ExternalServiceError` → **502**. Gateways that reject the
  schema parameter fall back to prompt-enforced JSON automatically.
- **Response contract**: field-by-field mapping to the v7 design, and the client
  wiring it implies, is in [ai-card-magic-contract.md](docs/ai-card-magic-contract.md).
  Renaming a `MeaningSuggestion` field is a breaking change for that screen — update
  the doc in the same commit.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
