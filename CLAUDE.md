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

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
