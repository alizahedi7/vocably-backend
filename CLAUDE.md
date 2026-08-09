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
  `categories`, `words`. All GET, all platform-wide, none mutate — with one
  deliberate exception: `PATCH /admin/decks/{id}/publish` curates Explore.
  Publishing is admin-only because there is no report path and no moderation
  queue; an open publish button without one is an unreviewed-content problem
  rather than a feature. Opening it to deck owners later is a permission
  change, not a migration.
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

### Lookup cache

Lookups repeat heavily across the user base, so `look_up_meanings` is served from a
shared Postgres cache — `CachingAIService`, a decorator over the `AIService` port, so
it applies to every provider and `AIStudioService` stays unaware of it. Stories are
never cached (their word set is one learner's Leitner boxes).

- **Key**: `(normalized input, native_language, age_bucket, PROMPT_VERSION)`, hashed.
  `interests` is deliberately **not** in it — themed examples would make every key
  unique and the cache would stop existing. `AgeRange`'s eight values collapse to three
  buckets, since only child/teen/adult changes the text.
- **`PROMPT_VERSION` in [prompts.py](app/infrastructure/ai/prompts.py) MUST be bumped
  with every prompt or lookup-schema change.** It is part of the key, so bumping it
  retires every card the old prompt wrote — no purge, no migration. Forgetting is the
  one way a prompt improvement silently fails to reach existing learners.
- **Two tables**: `ai_lookup_entries` holds the senses keyed by *resolved* term;
  `ai_lookup_aliases` holds one row per thing a learner *typed*, with its own
  `status`/`notice`. So a typo, a sentence, and the correct spelling share one
  paid-for entry. Inputs over `MAX_ALIAS_INPUT_CHARS` get no alias — a sentence is
  never retyped, and long free text is the only input likely to carry something
  personal.
- **Never user data.** No `user_id` anywhere in these tables, and a learner's edits to
  a card go to `words` and never back into the cache — one person's wording must not be
  served to everyone. Only what a provider produced is stored, at call time, whatever
  the learner does next.
- **Never load-bearing.** Every cache read/write is best-effort: a failure is logged
  and the provider's answer is served anyway. Stored payloads are re-validated on read
  ([lookup_cache_payload.py](app/infrastructure/db/lookup_cache_payload.py)) and a row
  this deploy can't read counts as a miss.
- `AI_CACHE_ENABLED=false` bypasses it for debugging a provider in isolation. Leaving
  it off in production means paying full price for every repeat of every word.

### Phonetics on the card

`words.phonetic` holds the IPA shown under the term on the card front. It is
**carried, never generated** — the dictionary supplies it, and it is left blank
rather than guessed, because a confidently wrong transcription teaches a learner
to mispronounce a word. Nothing may ask a model for one.

- **NULL and `""` are different, and the difference is load-bearing.** NULL means
  *no answer yet*; `""` means *the dictionary answered and this word has no IPA*
  — roughly a third of them. Without the distinction the backfill would re-fetch
  the same permanent misses nightly, forever. Both render as nothing on the
  client, which never has to tell them apart.
- **The client supplies it for free.** `POST /words` accepts `phonetic`, and the
  value came from the `/ai/lookup` that produced the card, so no extra call is
  made on the write path.
- **`vocably.ai.backfill_phonetics`** ([phonetics.py](app/tasks/phonetics.py))
  covers everything else: hand-written cards, rows older than the column. It is
  keyed by **term, not card** — one lookup answers for every learner who typed
  that word — and it is a no-op while `DICTIONARY_ENABLED` is off, or a
  background job would be making calls the request path is configured not to.
- **A lookup that fails is never recorded as "this word has no IPA".** The
  dictionary port collapses a miss and an outage into the same `None`; writing
  `""` there would let one bad afternoon permanently silence a set of cards. Such
  terms stay NULL, which is why `list_terms_missing_phonetic` orders **randomly**
  — under any stable order a few unknown words would sit at the head of the queue
  and consume every run.
- **Editing `term` clears `phonetic`** unless the same request supplies a new one
  (`WordService.update`). A transcription of the old spelling is a wrong
  transcription. The client applies the same rule to its add-word form.
- Copies from Explore carry it: how a word is pronounced is a property of the
  word, not of whose deck it sits in.

## Shared decks, and where study progress lives

A deck is shared as **the same deck**, not a copy: a word an editor adds is a
word every member sees, while each member holds their own boxes against it.
That is impossible while progress lives on the card, which is why `words` was
split.

```
words(id, deck_id, created_by_user_id, unit_id, term, meaning, …)   -- the card: shared
word_progress(user_id, word_id, deck_id, box, due_at, review_count, …)  -- per learner
  PRIMARY KEY (user_id, word_id)
```

- **`words.created_by_user_id` is attribution and nothing else.** It is never
  an authorization check. Who may read or edit a card is deck membership. If you
  find yourself comparing it to `current_user.id`, that is the bug.
- **Membership is the only access check.** `deck_members(deck_id, user_id, role)`
  answers it, through `DeckAccess`
  ([deck_access.py](app/application/services/deck_access.py)) — one place, on
  purpose, because getting it wrong in one of four places is a data leak.
  `decks.user_id` is the creator column and must not be read for access.
  Roles: owner manages members and deletes the deck; editor changes words and
  units; viewer studies. An unknown role parses to **viewer** — fail closed.
- **A non-member gets 404, never 403.** A 403 confirms that a deck or card with
  that id exists, which is exactly what someone probing ids wants to learn. A
  *member* who lacks the role for an action does get 403 — they already know it
  exists. Grading is always allowed to any member: study is not an edit.
- **Deleting is the owner's; leaving is everyone else's.** `DELETE /decks/{id}`
  is `require_manage`, so an editor with every right over the words still
  cannot destroy the deck they live in. The counterpart is
  `DELETE /decks/{id}/leave` (`DeckSharingService.leave`), which removes exactly
  one `deck_members` row: the deck leaves *your* list and nothing is deleted —
  the Telegram rule, and the reason a member never needs the owner to notice.
  The owner is refused with **409**, not obeyed: leaving would strand a class's
  deck with nobody who can manage it. Progress rows stay behind, as in
  `remove_member`, so rejoining restores the boxes.
- **`GET /decks` carries `role`/`is_owner`.** It comes off the membership row
  the deck list is already joined to (`list_for_user_with_role`), so the client
  can offer "delete" or "leave" per deck without one `/membership` request each.
  It is a convenience for the UI and never an authorization decision — the
  server re-checks on every write.
- **Progress rows are created lazily.** Only `grade` and `start` write one, via
  an upsert. A missing row reads as `WordProgress.unstudied` — box 1, due now,
  counters zero — so sharing a 500-word deck with a class of thirty writes
  *nothing*. Reads are `words LEFT JOIN word_progress`, and the `user_id`
  predicate belongs in the **ON clause**: in `WHERE` it silently becomes an
  inner join and every never-studied word disappears, which is most of them.
- **`word_progress.deck_id` is a live mirror** of `words.deck_id`, kept in step
  by `SqlAlchemyWordRepository.update` when a card moves deck. It exists so
  per-deck aggregates and the roster never join `words`. This is deliberately
  the *opposite* of `word_reviews.deck_id`, which is frozen at review time —
  do not unify them.
- **Cascades carry the policy.** `ON DELETE CASCADE` from `users` lands on
  `word_progress`, never on the card: a member leaving must not delete a class's
  vocabulary. `words.created_by_user_id` is **`ON DELETE SET NULL`** and
  nullable — attribution is exactly what may be lost when someone leaves, and a
  card an editor wrote belongs to the deck. Progress rows survive a member
  leaving a deck, so rejoining restores their boxes; every aggregate is scoped
  through `deck_members`, so those rows are invisible until then.
- **No user-facing request may scan `words` per learner.** `/study/overview` and
  the deck list both fold a single `tally_by_deck_and_box` result. The overview
  used to fetch every due row and count it in Python; do not reintroduce that.
  The roster is the other trap: it batches its member lookup, and
  `test_deck_sharing.py` counts SQL statements to keep it that way.
- **Account deletion (`DELETE /users/me`) is an application rule, not an FK.**
  It refuses with 409 while the caller owns a deck someone else is in, naming
  the decks. A foreign key cannot see whether a deck is shared, and the column
  that would destroy it is `decks.user_id` — `RESTRICT` on
  `words.created_by_user_id` was tried and made *every* account undeletable,
  because Postgres checks it before the deck cascade removes the user's own
  cards. **Order matters in the flow**: delete the user's own decks first, or
  the `SET NULL` fires against cards whose deck is being cascaded away in the
  same statement and fails on the deck foreign key.

### A new word's first review is tomorrow

A card is **never due on the day it is added**. Writing a word down is already
the first exposure, and answering it thirty seconds later tests short-term
memory rather than recall — the interval is the whole mechanism, which is why
every box interval starts at a day and the first one does too.

- **The boundary is the learner's day, not a 24-hour clock.**
  `WordProgress.first_due_at(day_start)` is `day_start + 1 day`, where
  `day_start` is `calendar.day_start_for(user.timezone)`. A word added at
  breakfast and one added at midnight both come up in tomorrow's reviews,
  whenever the learner sits down. `now + 24h` would hide a word added at 9am
  from someone who studies at 8am, which reads as the app losing it.
- **No row is written to say so.** A missing progress row still means
  "unstudied", and `WordProgress.unstudied` now derives the due date from
  `words.created_at`: added today → due tomorrow, added earlier → due now. In
  SQL that is one comparison, `_is_due()`: `wp.due_at <= now` when a row exists,
  otherwise `words.created_at < day_start`. The laziness is untouched.
- **`day_start` is a keyword on every read that depends on it** — `list_due`,
  `list_for_user`, `get_for_user`, `tally_by_deck_and_box` — because timezones
  belong to the service layer. `StudyService`, `WordService` and `DeckService`
  all resolve it from the user (which is why `DeckService` now takes a
  `UserRepository`). Passing `None` accepts a UTC boundary and is only honest
  for reads that ignore due dates, like the story generator's word list.
- **Starting a word is meeting it**, so `start_words` schedules its first
  review for tomorrow too, not for the moment of the tap.
- **Same-day study is still possible, deliberately.** `build_session` falls back
  to the learner's words when nothing is due, so "practice anyway" works the
  evening someone adds their first ten. What changed is that the *queue* no
  longer pushes them.
- Tests that want a due queue call `sleep_on_it()` (tests/api/conftest.py),
  which moves the cards back a day rather than the clock forward.

### A saved deck's words wait to be started

`deck_members.self_paced` decides what a *missing* progress row means, and it is
the one place the lazy-row design carries a second meaning:

| `self_paced` | no progress row means | set by |
|---|---|---|
| `false` | unstudied — box 1, due now, in the boxes (what it always meant) | `DeckService.create`, i.e. every deck someone builds |
| `true` | **not started** — not due, not counted, not in a session | Explore copy, accepted share, `add_member`, invite code |

The problem it solves: saving "504 Essential Words" used to drop five hundred
cards into one day's review queue, because every one of them read as new-and-due
the moment the copy landed. The queue is the product; burying it is how someone
stops opening the app.

- **The whole feature is one SQL expression**,
  `SqlAlchemyWordProgressRepository._is_started()` — a progress row exists, or
  the membership is not self-paced. Every read composes it. A query that forgets
  it either hides a learner's own new words or floods them with a stranger's.
- **`tally_by_deck_and_box` groups by it rather than filtering on it.** The deck
  screen still says "504 words" while the boxes say "12 of them are yours", so
  `DeckView` carries both `word_count` (the deck's size, the same for every
  member) and `started_count`. `progress_pct` is measured over *started* cards —
  against the whole deck it would read 0% for months and tell the learner their
  work counted for nothing.
- **`POST /decks/{id}/start`** takes `word_ids`, `unit_id` and/or `count`, which
  compose: "these three", "all of Unit 3", "the next ten", or — with no selector
  at all — the whole deck. `require_read` is enough: what it writes is the
  caller's own queue, so a viewer of a class deck may start words in it exactly
  as the owner may. Idempotent, and it never resets a box someone has moved.
- **`POST /decks/{id}/unstart`** is the undo behind "Added 20 words · Undo". It
  deletes only rows with `review_count = 0`: a card that has been answered holds
  real progress, and an undo is not a delete.
- **"The next ten" is the next ten of the book.** `list_unstarted` orders by
  `(created_at, id)` ascending — the exact reverse of the word list's
  `(created_at, id)` descending — so the two can never disagree, ties included.
  That is also why `copy_deck_to` now **preserves each card's `created_at`** and
  reads the source in order: stamping five hundred copies with one timestamp
  left the deck in uuid order and threw the course's sequence away.
- **Adding your own card to a self-paced deck starts it.** `WordService.create`
  reads `member.self_paced` and writes the row. Whoever typed the word is
  plainly learning it; asking them to then add it to their own boxes would be
  the second step this feature exists to remove.
- **Nothing was backfilled.** Every membership that existed before the migration
  is `false`, so no one's due queue, streak or memory strength changed the night
  it deployed. Self-pacing applies to decks saved from then on.
- **Old clients degrade quietly.** `WordOut.started` is additive and an unstarted
  card still carries box 1 / due-now placeholders, so an Android build that
  predates the field renders the deck exactly as it always did — it just sees
  nothing due until the learner starts something on a newer client.
  `tests/api/test_self_paced_decks.py` holds all of it.

### Writes that two requests can reach at once

A double-tapped button is the normal case, not an edge case, and each of these
was a real bug found by hammering a running server:

- **Grades increment counters in SQL**, never write back a value read earlier.
  Read-modify-write made six simultaneous grades land as one, leaving
  `review_count` disagreeing with `word_reviews`. `record_grade` returns the row
  that actually landed, so the response is the truth rather than the hope.
  `box`/`due_at` stay last-writer-wins: one card can only have one schedule.
- **Memberships insert with `ON CONFLICT DO NOTHING`** (`add_if_absent`).
  Check-then-insert turned a second tap on an invite link into a 500. `DO
  NOTHING` rather than `DO UPDATE`, so re-tapping a viewer link cannot demote a
  member the owner has since promoted.
- **The invite row upserts on `deck_id` and never rewrites `code`** — reopening
  a link must not invalidate one already handed to a class.
- **A handle conflict is translated at the constraint**, in
  `SqlAlchemyUserRepository.update`. `/users/username-available` is advisory;
  the unique index decides, and the loser of a race gets a 409 with copy rather
  than a stack trace.

`tests/api/test_concurrency.py` holds all of these. One of them needs truly
independent sessions and is skipped on the SQLite run.

### Units

`deck_units` is optional grouping. A deck with no units renders exactly as it
did before the feature, and a card may belong to **no** unit — there is no
"uncategorised" unit. `words.unit_id` is `ON DELETE SET NULL`, and that **is**
the product rule: deleting a unit keeps its cards and drops them back into the
deck, which is why the client asks for no confirmation. Order by `position`,
never by name — "Unit 10" sorts between 1 and 2.

`PATCH /words/{id}` treats an **omitted** `unit_id` as "leave it alone" and an
explicit **null** as "remove from its unit". `x is None` cannot tell them apart;
the router reads `model_fields_set`. Conflating them lets a client older than
units silently ungroup every card it edits.

### Invite links

An invite code is a **bearer credential**: CSPRNG, ~65 bits, unique-indexed.
The client's local stand-in derives a 6-character code from `deckId.hashCode`
and is a UI placeholder — never copy it. `deck_invites` is keyed by deck, so
re-opening a closed link returns the same code; one already handed to a class
must keep working. A bad code, a closed one and an expired one all answer the
same 404. `POST /decks/join` is rate-limited per user *and* per IP.

**Known client gap:** nothing yet parses `https://app.vocably.ir/join/<code>`
into a `joinByCode` call, so the flow is copy-and-paste until the PWA gets a
`/join/:code` route and Android gets an App Link.

### Days, weeks, and the activity rollup

Every "today" here is a local question — a streak, a daily goal and the roster's
weekly figures all turn on where a day starts, and a learner in Tehran was
getting a UTC boundary at 03:30 local. `users.timezone` holds an IANA name
(NULL means UTC) and
**[calendar.py](app/domain/services/calendar.py) is the only place a day or week
boundary is computed.** Weeks start Monday, matching the client. Do not scatter
`date.today()` through services.

`daily_deck_activity(user_id, deck_id, day, reviews, mastered)` backs the
roster's weekly numbers. It exists because `CLAUDE.md` forbids user-facing
aggregation over `word_reviews`, and a roster of thirty students would scan that
log thirty times. Counters ride along on the transaction the grade already
opens, bucketed by the learner's local day; `mastered` counts only the
transition *into* box 5 (`ReviewApplied.became_mastered`). The roster is two
grouped queries for the whole class — the N+1 here is the obvious way to write
it wrong.

### Handles

`users.username` is the one user-chosen string other people type. Stored
already-lowercased behind a unique index — normalising in the application would
let two casings both exist. Reserved names
([usernames.py](app/domain/services/usernames.py)) are refused as *invalid*
rather than taken, so the availability endpoint answers identically whether or
not anyone holds them. `/users/username-available` is capped **per user through
Redis**: the in-process limiter multiplies its budget by the worker count, which
is fine for SMS spend and wrong for an endpoint that answers "does this person
exist" for any string. An unreachable Redis degrades to per-worker, never open.

### The error envelope

4xx bodies carry **both** `error.code`/`error.message` and a top-level `detail`,
because the two clients read different keys: vocably-admin reads `error.code`,
and the Flutter client's `ApiClient._extractDetail` reads `detail` and otherwise
shows "Request failed (409)". 4xx messages on the sharing, units and friends
paths are **user-visible copy** — write them as such, and keep them in English.

## Explore, sharing, friends and XP

**Saving from Explore takes a copy; sharing with a person shares the deck.**
That distinction is the whole design and is easy to invert by accident:

- `POST /decks/public/{id}/import` **copies** the deck, its units and its words
  ([deck_discovery_repository.py](app/infrastructure/db/repositories/deck_discovery_repository.py)).
  A published deck is a starting point, so editing your copy must not change
  anyone else's. The copy is private and credits the copier — inheriting
  `is_public` would let one popular deck spawn a hundred listings, and
  inheriting the original author would let their account deletion touch rows in
  a deck they have nothing to do with. Progress is never copied: an absent row
  already reads as box 1, due now.
- `POST /decks/{id}/share` + `POST /decks/shared/{id}/accept` make the
  recipient a **member of the same deck**. `deck_shares` is the pending offer;
  accepting writes a `deck_members` row. Only someone who could already invite
  may share, so a viewer cannot hand a teacher's deck around. Declining deletes
  the offer and tells the sender nothing.
- A share id that is not yours **404s**, like everything else keyed by id.

**Friends are a recency list, not a social graph.** One-directional and needing
no consent, because they reveal nothing the sharer did not already know — they
typed the handle. **Handle lookup is exact-match only**: no prefix search, no
"find people" endpoint. Adding one is a product decision with a consent
question attached, not a convenience. Sharing links the recipient
automatically, which is why a handle is only ever typed once.

**XP is a ledger plus a counter**, the same hybrid as the review history.
`xp_events` is append-only; `users.xp` is what every request reads and is
incremented **in SQL** — a read-then-write would let two awards clobber each
other. It is deliberately absent from `apply_user`, so a profile save can never
overwrite it.

- The award table in [xp.py](app/domain/entities/xp.py) mirrors the client's
  `progress_rewards.dart` exactly, as does `total_xp_for`. A disagreement shows
  up as a level that changes when the learner switches device.
- **Nothing accepts a client-supplied point total.** The daily goal is derived
  server-side from `daily_deck_activity`, and "pays once a day" is a *partial
  unique index* on `(user_id, action, day)` — an application check would let two
  sessions finishing together both collect.
- Grades pay by `source`: a drill is worth more than a review, and a *wrong*
  drill answer still pays, because turning up to be tested on your weakest words
  is the behaviour worth rewarding.

**Badges have no table and no endpoint.** They are a pure function of
`mastered_count` on `/study/overview` — box 5 across every deck, which is a
different number from `learned_count` (boxes 4 and 5). A derived badge cannot
go stale or disagree with the words.

## Review history

Every press of Again/Hard/Good/Easy appends one immutable row to `word_reviews`.
The card in `words` holds only its *current* state, so without this log a word
reviewed ten times flawlessly and one failed ten times are indistinguishable —
both just read `review_count == 10`. None of it is reconstructible after the
fact, which is why the log exists before the features that will consume it.

- **Hybrid by design.** Immutable events in `word_reviews`; summary counters
  (`lapse_count`, `consecutive_correct`, `first_reviewed_at`, `mastered_at`,
  `last_grade`) updated in place on `words` by
  [`Word.apply_review`](app/domain/entities/word.py). The counters are what the
  product reads — "your hardest words" is `lapse_count / review_count`,
  time-to-mastery is a column subtraction — so **no user-facing request should
  ever aggregate over `word_reviews`**. They ride along on the UPDATE the grade
  already issues, so they cost no extra write. Longer-range analytics belong on
  a rolled-up daily aggregate, not on scans of the raw log.
- **Ordering matters in `grade`.** [`ReviewEvent.from_review`](app/domain/entities/review_event.py)
  must be called *before* `apply_review`; it captures the pre-review box, due
  date and elapsed time that `apply_review` overwrites. The factory exists so
  that requirement lives in one place.
- **Written in the same transaction as the card update** — deliberately unlike
  the lookup cache above. That cache is derived data, so a lost write costs one
  repeat API call; a lost review is gone for good and leaves the log disagreeing
  with `review_count`. A failure here must fail the request.
- **`elapsed_seconds` + `grade` are the point.** They are what a fitted
  scheduler (FSRS and relatives) trains on, and the only reason it will ever be
  possible to replace the fixed ladder in
  [leitner.py](app/domain/services/leitner.py) with a per-learner model.
  Neither is recoverable later, at any price.
- **`ReviewGrade.ordinal` is a frozen wire format.** Grades are stored as
  `smallint`. Those numbers sit in rows that outlive any deploy — never renumber
  them; a new grade takes the next free ordinal.
- **Partitioned by month on `reviewed_at`** (Postgres only; the ORM model can't
  express it and doesn't try, so `create_all` in tests yields a plain table —
  the partitioning contract is covered by
  [test_review_partitioning.py](tests/api/test_review_partitioning.py), which
  runs the real migrations). Partitions do not appear on their own: **`make
  partitions` must run on a schedule.** `make partitions prune=1` drops months
  past `REVIEW_HISTORY_RETENTION_MONTHS` and is destructive, so it is never
  implied by a plain run. `word_reviews_default` is a safety net that must stay
  empty — the script exits non-zero if it isn't, because creating a partition
  overlapping rows stuck there locks and rescans it.
- **This is user data**, unlike the lookup cache. It must never flow into the
  shared `ai_lookup_*` tables, and AI features should be fed *summaries*
  computed server-side, never a raw event stream handed to a provider.
  `ON DELETE CASCADE` from both `users` and `words` means erasure and card
  deletion really erase.

## Background tasks (Celery)

A second entry point into the same codebase, alongside the FastAPI app. Tasks are
adapters exactly as routers are: unpack a message, call the application layer,
translate the outcome. Domain and application code stays unaware Celery exists.

- **Two processes.** `make worker` executes tasks and scales freely;
  `make beat` emits scheduled ones and **must never exceed one replica** — beat
  is a clock, and two of them make every scheduled task fire twice.
- **Redis is the broker**, `redis://redis:6379/0` in compose. No result backend
  by default (`CELERY_RESULT_BACKEND=""`): nothing reads these tasks' return
  values, and storing them would grow Redis forever. Set it when something does.
- **Register new task modules in `TASK_MODULES`**
  ([celery_app.py](app/tasks/celery_app.py)). A task outside that list is never
  imported, and beat scheduling it fails with "unregistered task" only when it
  first fires.
- **Queues split by what would be blocked**, not by feature: `maintenance` is
  small and latency-sensitive, `ai` is slow, external and bursty. Sharing one
  queue is how a backlog of AI work silently stops partition maintenance.
  Routing is by task-name prefix (`vocably.ai.*` → `ai`), so **name AI tasks
  `vocably.ai.<something>`** or they land on the default queue.
- **`run_async` is mandatory for async work in a task**
  ([runtime.py](app/tasks/runtime.py)). Celery workers are synchronous; this
  codebase is not. `asyncio.run` builds a new event loop per call while the
  SQLAlchemy engine is process-global and its pooled asyncpg connections belong
  to the loop that opened them — so the helper disposes the engine after every
  run. Calling `asyncio.run` directly instead makes the *second* task in a
  worker pick up a connection tied to a dead loop, which surfaces as random,
  very hard to trace database flakiness.
- **`task_acks_late` is on**, so a worker killed mid-task (deploy, OOM, spot
  reclaim) has its work redelivered rather than dropped. The price is that
  **every task must be safe to run twice.**
- Scheduled entries carry `expires`, so a worker returning from a long outage
  drops the missed runs instead of replaying them all at once.

### Scheduled work

`vocably.maintenance.review_partitions` runs daily at
`REVIEW_HISTORY_MAINTENANCE_HOUR` (UTC, default 03:00) and calls the same
`maintain()` as `make partitions` — one implementation, so the cron job and an
operator at a terminal cannot drift apart.

**It does not prune by default.** `REVIEW_HISTORY_AUTO_PRUNE=false` means
expired partitions are reported in the logs and never dropped. Turning it on
makes a background job delete learners' history irreversibly, which should be a
deliberate retention policy, not a side effect of installing a scheduler.

`vocably.ai.backfill_phonetics` runs daily at `PHONETIC_BACKFILL_HOUR` (UTC,
default 04:00) on the **AI queue**, and looks up `PHONETIC_BACKFILL_BATCH_SIZE`
terms per run. Deliberately not the maintenance hour and not the maintenance
queue: it makes hundreds of outbound requests, and a backlog of those must not
delay a partition. It does nothing while `DICTIONARY_ENABLED` is off — see
[Phonetics on the card](#phonetics-on-the-card) for why the batch is bounded and
the ordering is random.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).

## The lexicon, and pre-built decks

Two new things, and the distinction between them is the one worth protecting.

**The lexicon is durable shared knowledge about a word**; the lookup cache above
it is a disposable record of requests. They are not the same table and must not
be merged:

| | `ai_lookup_entries` (cache) | `lexemes` / `lexeme_senses` (lexicon) |
|---|---|---|
| Keyed by | prompt version + language + age + input | the lemma |
| A prompt bump | retires it, by design | marks it stale, deletes nothing |
| Per-sense id | none — an opaque blob | stable, and a deck card points at one |
| Failure | best-effort, a miss | best-effort on read, durable once written |

Both are impersonal: **no `user_id` reaches either**, and a learner's edits to
their card go to `words` and never come back. What is new is permanence — a
published deck references a sense, so senses outlive the prompt that wrote them.

### Composition order is the whole design

```
AIStudioService
  └─ CachingAIService      # request-shaped, keyed by prompt version
      └─ LexiconAIService  # term-shaped, durable, survives a prompt bump
          └─ GroundedAIService
              └─ provider
```

The cache is **outside** so repeats cost one indexed read. The lexicon is
**inside** so it answers what the cache cannot — which, the day `PROMPT_VERSION`
is bumped, is every request. Swap the two and a prompt edit re-buys the entire
corpus. `tests/api/test_lexicon.py::test_a_prompt_version_bump_costs_nothing_for_a_word_already_known`
is the test that fails if anyone does.

`app/infrastructure/ai/factory.py` builds this chain, and **both** the API and
the deck-build worker call `lookup_chain()`. A builder with a private path to
the provider would stop reusing what learners already paid for, and the reuse
ratio reported on every job would become a lie.

### Senses, and the translation split

`lexeme_senses` holds the English half — definition, example, part of speech, and
the `context` chip. `lexeme_sense_translations` holds the learner-facing headline,
one row per language. That split is a cost decision: adding a second native
language re-buys short headlines, not the corpus. `LexiconService.record` writes
both halves and **tops up translations on senses it already holds**.

- `sense_key` is `slug(part_of_speech):slug(context)`, derived rather than
  random, because enrichment must answer "do we have this one?" without an AI
  call. `UNIQUE (lexeme_id, sense_key, register)` makes a duplicate write a no-op.
- **Positions are never renumbered.** A published deck may point at position 2.
- Max 4 senses is a product cap enforced in `LexiconService`, not a constraint —
  a constraint would fail an enrichment write nothing could recover from.
- `lexemes.phonetic` keeps the `NULL` vs `""` distinction from `words.phonetic`,
  for the same reason: without it the backfill re-asks permanent misses forever.

### Deck templates live in git, not the database

`content/decks/<slug>/{deck.yaml,words.yaml}`. A word list arrives as a **pull
request**, so ordering, lesson boundaries and sense pins are read by a human
before a token is spent. **The order is the product** for a course, so structure
is *asserted* (`expected_units`, `expected_words`, `words_per_unit`) and the
validator refuses a file that disagrees — a list that silently lost an entry is
otherwise invisible until a learner notices Lesson 12 has eleven words.

For copyrighted collections the pipeline stores **the headword list and lesson
boundaries only**, plus a `source.rights` note. There is deliberately no field in
which a source's definition or example could be pasted; a `hint` over 200
characters is refused for exactly that reason.

### Building

```
make deck-validate slug=…   # offline, free
make deck-plan     slug=…   # writes deck + units + plan, spends nothing
make deck-build    job=…    # spends (queue=1 hands it to Celery instead)
```

- **The plan is rows.** `deck_build_items`, written once, `UNIQUE (job_id,
  position)`. Building is a *state transition*, never an insert, which is what
  makes `task_acks_late` redelivery harmless and resume a matter of re-running.
- **Claims commit before any provider call**, and results commit after. No
  transaction is ever held open across the network — under a 500-word build that
  is how the connection pool dies.
- **`vocably.ai.build_deck` re-queues itself** rather than fanning out. Chords
  need a result backend and `CELERY_RESULT_BACKEND` is empty by design; the item
  table is a better coordinator anyway, and batch size × worker concurrency *is*
  the rate limit.
- **Failure is per item.** Three attempts with 2/4/8-minute backoff computed on
  the row, then `failed`, and the job ends `PARTIAL` — 501 good cards and three
  holes is worth reviewing, not restarting. `reset_failed` retries just those.
- **A job carries its own `category` and `strategies`**, copied at plan time. The
  template file is never re-read mid-build: the plan is already in the database
  and must stay buildable if the file moves.
- **`content_version` is pinned at plan time**, so a deploy that bumps a prompt at
  word 300 of 504 cannot write the two halves under different prompts.

### Sense selection, and why review is short

Deterministic, ordered, first match wins, and **no AI call is made to rank
senses**: `explicit` (a template pin) → `hint` (token overlap against a gloss) →
`category` (a hand-written prior per deck category) → `first`. Every item records
which strategy chose and how well it scored, and the admin review queue is
exactly "failed, flagged, or chosen by a strategy that guesses". On a good build
that is a few dozen rows out of five hundred, which is the difference between
review happening and not.

Enrichment fires when the deck **asked for a sense and did not get it** — not
only when nothing was selectable, because with `first` in the chain something
always is. It is capped at **one call per item, ever** (`deck_build_items.enriched`,
set *before* the call), appends only, and returning nothing is a correct answer.

### Pronunciation on a built deck

`DICTIONARY_ENABLED` used to gate two unrelated decisions: whether cards are
*worded* from a dictionary (grounding — a product choice that changes what every
learner reads) and whether the app may look up an IPA at all. **`PHONETICS_ENABLED`
separates them.** Turning it on lets a deck carry pronunciation without
re-wording a single card; `DICTIONARY_ENABLED` implies it, since the grounded
path already fetches the entry an IPA comes from.

The build fills `lexemes.phonetic` inline rather than leaving it to the nightly
backfill — the lookup is free, ~27 ms, and Redis-cached across every learner, and
the alternative is publishing a deck with no pronunciation and filling it in days
later. Everything else is carried over from `words.phonetic` unchanged: a model
is **never** asked for a transcription, `NULL` and `""` stay different, and a
miss and an outage both leave `NULL` so one bad afternoon cannot permanently
silence a set of cards.

### Publishing is still a separate, deliberate act

Nothing in this pipeline sets `decks.is_public`. A build creates a private deck
and leaves it private; `PATCH /admin/decks/{id}/publish` is the only thing that
puts it in Explore. That is what makes a half-built deck invisible by
construction rather than by vigilance.

Official decks are owned by a system account (`CONTENT_OWNER_PHONE`), created on
first plan. It is never a person and must never be pointed at account deletion.

### Regeneration is always explicit

A prompt bump costs nothing and changes nothing: the cache retires, the lexicon
reports `stale` on `/admin/lexicon/stats`, and no job is enqueued. Cards already
written hold their own copy of the text — deliberately, because a learner may
edit theirs — so `words.lexeme_sense_id` is provenance for an opt-in refresh, not
a live read path.

### Single-flight is money, never correctness

Concurrent generation of one word is deduplicated by a Redis `SET NX` lease.
Correctness comes from the lexicon's unique constraints, so this can only waste a
call. It is therefore **disabled for the whole process after its first failure**:
a refused connection costs real latency on every word (measured at ~11s/word with
Redis down), and a command cancelled by its timeout can leave a pooled connection
that blocks the next caller indefinitely. `pg_advisory_xact_lock` was rejected —
it would hold a database connection for the whole provider call.
