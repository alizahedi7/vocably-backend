# Backend brief — vocably-mobile `feat/design-v10`

This is the implementation brief for the backend work that the mobile/web
client's `feat/design-v10` branch needs. Read it end to end before writing
code: the first section changes the shape of `words`, and almost everything
after it depends on that change.

**Work on a branch: `feat/design-v10`** (same name as the client's, so the two
are easy to pair). Follow this repo's own conventions from `CLAUDE.md` —
hexagonal layering (router → application service → domain port →
SQLAlchemy repository), Conventional Commits, one logical change per commit, an
Alembic migration committed together with the model change that required it,
tests beside the code they test, and `graphify update .` at the end.

## The situation

The client has been redesigned and now expects a considerably larger backend.
Every new surface was built against a service **interface** with a
`Local*` on-device implementation, so the app runs today with nothing new on
the server. Each `Rest*` implementation is already written against the paths
below — they are the contract, not a suggestion. Where a path or field name
here disagrees with your instinct, prefer changing it *in both repos in one
pair of commits* over silently answering a different shape.

**There are real users with real data.** Existing rows must survive every
migration in this brief, and every existing Android install keeps calling the
*old* endpoints for weeks (Play review + rollout + people who never update).
So:

- Additive changes only, unless a break is called out here explicitly.
- Existing response shapes must stay byte-compatible in the fields old clients
  read. New fields are fine; renamed or removed fields are not.
- Every migration needs a data step, not just a schema step, and must be
  correct for a user who has 3 000 words and one who has none.
- Test the *upgrade path*, not only the end state: this repo already runs real
  migrations in `tests/api/test_review_partitioning.py`; do the same for the
  `words` split below.

---

## 1. The hard part: study progress must stop living on the card

**Do this first. Nothing else in this brief is safe until it is done.**

### Why

Today `words` carries both the card *and* one user's study state:

```
words(id, user_id, deck_id, term, meaning, definition, example, sense_label,
      box, due_at, review_count, last_reviewed_at,
      lapse_count, consecutive_correct, first_reviewed_at, mastered_at, last_grade)
```

One row = one card = one learner's progress. The client now shares a deck as
**the same deck**, not a copy: a word an editor adds is a word every member
sees, while each member has their own boxes against it. That is impossible in
the current schema — sharing a deck would either share one set of boxes between
thirty students or fork the words into thirty copies, and both are wrong.

### The target shape

Split card content from per-user study state.

```
words(id, deck_id, created_by_user_id, unit_id,           -- the card: shared
      term, meaning, definition, example, sense_label,
      created_at, updated_at)

word_progress(user_id, word_id,                            -- the study state: per user
      box, due_at, review_count, last_reviewed_at,
      lapse_count, consecutive_correct, first_reviewed_at, mastered_at, last_grade,
      created_at, updated_at)
  PRIMARY KEY (user_id, word_id)
```

Notes that matter:

- `words.user_id` becomes `words.created_by_user_id`, **attribution only** —
  never an authorization check again. Authorization is deck membership from
  section 4 onward. Grep for every current use of `words.user_id` and classify
  each one as "which user's progress" (→ `word_progress.user_id`) or "who
  added this" (→ `created_by_user_id`); getting one wrong is a data-leak bug,
  so do this mechanically rather than by eye.
- `ON DELETE CASCADE` from `users` must land on `word_progress`, **not** on the
  card. A member leaving or deleting their account must not delete a class's
  vocabulary.
- Indexes: the due-queue query becomes
  `Index("ix_word_progress_user_due", "user_id", "due_at")` — move
  `ix_words_user_due` there. Keep `words(deck_id)`. Add `words(unit_id)`.
- `word_reviews` keeps FKs to both `users` and `words` and needs no change.
  Its cascade semantics stay as documented in `CLAUDE.md`.

### Progress rows are created lazily

Do **not** fan out a `word_progress` row per member per word when someone joins
a deck — a teacher sharing a 500-word deck with a class of 30 would write
15 000 rows for people who may never open it.

- Reads `LEFT JOIN word_progress` and substitute defaults for a missing row:
  `box = 1`, `due_at = now()`, all counters zero, all timestamps NULL. A word
  nobody has studied is due immediately, which is exactly what "new" means.
- Writes (`grade`) upsert (`INSERT … ON CONFLICT (user_id, word_id) DO UPDATE`).
  `ReviewEvent.from_review` still runs *before* `apply_review`, and the event
  row is still written in the same transaction — both rules in `CLAUDE.md`
  survive this refactor unchanged.
- `Word.apply_review` should move to operate on the progress entity, not the
  card. Keep it one function; do not let scheduling logic spread into the
  repository layer.

### The data migration

One Alembic revision, and it must be idempotent-safe and reversible:

1. Create `word_progress`.
2. `INSERT INTO word_progress (…) SELECT user_id, id, box, due_at, review_count,
   last_reviewed_at, lapse_count, consecutive_correct, first_reviewed_at,
   mastered_at, last_grade, created_at, now() FROM words` — every existing
   card yields exactly one progress row, for the user who owned it. No learner
   loses a single box.
3. Rename `words.user_id` → `created_by_user_id`, adjust the FK's `ondelete`
   from `CASCADE` to `SET NULL` (or `RESTRICT` — decide with section 4's
   account-deletion question), and drop the progress columns from `words`.
4. Move the index.

Do the column drop **in the same revision**, not "later": a period where both
copies exist and both are written is how they silently diverge. If the table is
large enough that one transaction worries you, say so and stage it as
expand → backfill → contract across three revisions with the application
writing both — but only if the row count actually justifies it. Check first;
this is a small product.

`downgrade()` must reverse it by folding each user's progress back onto their
own cards, and it is acceptable for the downgrade to lose progress rows for
words the user did not create — document that in the revision docstring.

### Backward compatibility for old clients

`GET /words`, `GET /study/session` and `POST /study/words/{id}/grade` must keep
returning **exactly the shape they return today**, with `box`, `due_at`,
`review_count` etc. read from the requesting user's progress row (or the
defaults above). An Android build from before this change must not be able to
tell that anything moved. Add a test that asserts the serialised word payload
still contains those keys.

### Definition of done

- Two users can hold different boxes against the same `words.id`.
- Deleting user A leaves user B's progress and every card intact.
- Every existing user's boxes, streak, due counts and review history read
  identically before and after the migration (write a test that captures a
  fixture DB's `/study/overview` output across the upgrade).

---

## 2. Users: a handle, and what they're learning

The client's first-run setup is now two steps, and it asks for a **handle** —
which sharing, friends and the roster all address people by.

### Schema (`users`)

| Column | Type | Notes |
|---|---|---|
| `username` | `String(20)`, **unique**, nullable | Lowercase. `^[a-z][a-z0-9_]{2,19}$`. Nullable because existing users have none — see backfill. |
| `target_language` | `String(64)`, nullable | The language being learned. |
| `proficiency` | `String(32)`, nullable | Free-form key from the client's `kProficiencyLevels`; do not enum it server-side, the list is a product surface that changes. |
| `study_time` | `String(32)`, nullable | Key, not a time. Pre-fills the Android reminder. |
| `timezone` | `String(64)`, nullable | IANA name, e.g. `Asia/Tehran`. See "days and weeks" below. |
| `xp` | `Integer`, default 0 | Section 7. |

Add a `citext`-style guarantee for the handle: store it already-lowercased and
add a unique index; do not rely on the application to normalise. Reserve a
small denylist (`admin`, `vocably`, `support`, `me`, `api`, `join`, `null`) —
`/join/<code>` and `/users/me` are real paths and a handle that collides with
one is a support ticket waiting to happen.

### Backfill for existing users

Existing accounts are onboarded and will never see the setup step that asks for
a handle. Leaving `username` NULL forever means sharing and the roster cannot
address them.

Do this in the migration, deterministically:

1. Slugify `name` the same way the client does (`AppState.slugifyUsername`:
   lowercase, non-`[a-z0-9_]` → dropped, must start with a letter, 3–20 chars).
2. On collision or an unusable result, append the shortest numeric suffix that
   is free, then fall back to `user_<first 8 of id>`.
3. Log a count of each strategy used.

Then let people change it: `PATCH /users/me` accepts `username`, validates
availability in the same transaction as the update (`SELECT … FOR UPDATE` or
just catch the unique violation and answer 409), and rate-limits changes to
something like one per 30 days — a handle other people have saved as a friend
should not be a rotating identifier. Old handles must **not** be immediately
re-claimable by someone else; keep a `username_history` table or a simple
`released_usernames(username, released_at)` quarantine.

### Endpoints

- `GET /api/v1/users/username-available?username=<s>` → `{"available": true}`.
  Already called by the client (`ApiRepository.checkUsername`) and it re-checks
  on Continue, so the answer must be cheap and correct, not cached.
  **Rate-limit it hard and per-user** (this repo has `core/rate_limit.py`): it
  is a handle-enumeration oracle otherwise. Answer `available: false` for
  denylisted and malformed input rather than erroring — the client treats a
  *failure* as `unknown` and lets the user through, which is deliberate (being
  unable to ask is not a rejection) but should not be reachable by typing.
- `PATCH /users/me` gains `username`, `target_language`, `proficiency`,
  `study_time`, `timezone`. All optional; omitted means unchanged.
- `POST /users/me/onboarding` gains the same fields.
- `GET /users/me` returns them. Keys are snake_case, exactly:
  `username`, `target_language`, `proficiency`, `study_time` (see
  `lib/models/user_profile.dart`).

### Days and weeks — decide this once, here

`users.last_studied_on` already computes a "day" and this brief adds
`reviewed_today`, the daily-goal XP award, and the roster's *weekly* figures.
All of them need a timezone, and the server currently has none — so a learner
in Tehran gets a day boundary at 03:30 local.

Store `users.timezone`, have the client send it (it can, on every profile
write and every sign-in — the device knows), default to `UTC` when unknown, and
compute **every** day/week boundary from it in one place —
a small `app/domain/services/calendar.py` or similar. Weeks start Monday, which
is what the client's `weekStart()` does. Do not scatter
`date.today()` through services.

---

## 3. Units: optional grouping inside a deck

A deck may group cards into units/lessons; most never will. The client's rule
is that a deck with no units renders exactly as it did before the feature
existed, and **a card may belong to no unit** — there is no "default" or
"uncategorised" unit.

### Schema

```
deck_units(id uuid pk, deck_id uuid fk→decks ON DELETE CASCADE,
           name varchar(40) not null, position int not null,
           created_at, updated_at)
  UNIQUE (deck_id, position)   -- or just index it; see below
words.unit_id uuid null fk→deck_units ON DELETE SET NULL
```

`ON DELETE SET NULL` **is** the product rule: deleting a unit keeps its cards
and drops them back into the deck. The client shows a toast saying how many
came loose and asks for no confirmation, precisely because nothing is lost —
do not add a cascade here.

Order is `position`, not `name`: "Unit 10" sorts between 1 and 2
alphabetically. New units get `max(position) + 1`. If you enforce
`UNIQUE (deck_id, position)`, reordering needs a deferred constraint or a
two-pass update — a plain index and server-assigned gaps is simpler and enough.

### Endpoints (already called by the client)

| Method | Path | Body / result |
|---|---|---|
| `GET` | `/decks/{deck_id}/units` | `[{id, deck_id, name, position}]`, ordered by `position` |
| `POST` | `/decks/{deck_id}/units` | `{name}` → the created unit |
| `PATCH` | `/units/{unit_id}` | `{name}` → the updated unit |
| `DELETE` | `/units/{unit_id}` | 204 |

All four require `canEditWords` on the deck (section 4). `name` is capped at 40
chars, trimmed, and must be non-empty.

### `unit_id` on words

- `POST /words` accepts `unit_id` (nullable, omitted by old clients).
- `PATCH /words/{id}` accepts `unit_id`, and this needs care: **omitting the
  key means "leave it alone", so `"unit_id": null` must mean "remove from its
  unit"**. The client already distinguishes these (`updateWord(clearUnit:)`
  sends an explicit null). Use `Optional`-style sentinel handling in the
  Pydantic schema (`model_fields_set` / a `Unset` marker), not `x is None`.
- Validate that the unit belongs to the same deck as the word. A `unit_id` from
  another deck is a 422, not a silent write.
- `GET /words` returns `unit_id` (`""`/absent is normal and means no unit).

---

## 4. Shared decks: one deck, many members, separate progress

The centrepiece. Two ways in: invite a person by handle, or hand out a link (a
teacher and a class). Roles decide what a member may change. Progress is per
member and always was — that is section 1's whole point.

### Schema

```
deck_members(deck_id uuid fk→decks ON DELETE CASCADE,
             user_id uuid fk→users ON DELETE CASCADE,
             role varchar(16) not null,        -- 'owner' | 'editor' | 'viewer'
             invited_by_user_id uuid null,
             joined_at timestamptz not null,
             created_at, updated_at)
  PRIMARY KEY (deck_id, user_id)
  INDEX (user_id)                              -- "decks shared with me"

deck_invites(deck_id uuid pk fk→decks ON DELETE CASCADE,
             code varchar(32) unique not null,
             role varchar(16) not null,
             is_open bool not null default true,
             created_by_user_id uuid,
             expires_at timestamptz null,
             created_at, updated_at)
```

The migration must **backfill an `owner` row in `deck_members` for every
existing deck** from `decks.user_id`. After that, `decks.user_id` is the
creator/attribution column and membership is the authorization source. Keep
both consistent, or better: make `decks.user_id` derived and stop reading it
for access checks entirely.

### Authorization — the one thing to get right

Every deck- and word-scoped route currently asks "is `deck.user_id ==
current_user.id`?". All of them must become "what is my role on this deck?".

Implement it **once**, as a FastAPI dependency in `app/api/deps.py` next to
`require_admin` — e.g. `DeckAccess = require_deck_role(minimum=...)` returning
the membership — and use it on every route. Do not re-derive it per handler.
Then:

| Capability | owner | editor | viewer |
|---|:-:|:-:|:-:|
| Read the deck, its words, its units, study it | ✅ | ✅ | ✅ |
| Add / edit / delete words, create / rename / delete units | ✅ | ✅ | ❌ |
| Invite, remove members, change roles, open/close the link | ✅ | ❌ | ❌ |
| See another member's detailed progress | ✅ | ❌ | ❌ |
| Delete the deck | ✅ | ❌ | ❌ |

Rules, each of which the client already assumes:

- An unknown role string parses to **`viewer`**, the least privileged
  (`DeckRole.parse`). Fail closed, both directions of the wire.
- A viewer is the **default** for a class: a student adding words to the
  teacher's deck is rarely what was meant.
- There is exactly one owner. Removing yourself as owner is not allowed;
  transferring ownership is a separate action (and can be out of scope for
  now — just make it impossible to orphan a deck).
- Grading a word in a deck you can read is always allowed — study is not an
  edit.
- **A viewer must not be able to reach another member's word progress or
  review history through any route.** Write the negative tests.

### Account deletion and shared decks

Right now `decks.user_id` is `ON DELETE CASCADE`, so deleting the owner of a
class deck destroys it for thirty students. Decide and implement one of:

1. Block deletion while the user owns a shared deck (simplest, honest).
2. Transfer ownership to the longest-standing editor, else delete.

Either is defensible; silently cascading is not. Whatever you pick, state it in
`CLAUDE.md`.

### Endpoints

| Method | Path | Notes |
|---|---|---|
| `GET` | `/decks/{id}/membership` | The `DeckMembership` shape below. **404 when never shared** — the client treats that as "not shared" and not as an error. |
| `POST` | `/decks/{id}/members` | `{username, role}` → membership. 422 on own handle / unknown handle, 409 if already a member. |
| `PATCH` | `/decks/{id}/members/{username}` | `{role}` → membership |
| `DELETE` | `/decks/{id}/members/{username}` | → membership |
| `POST` | `/decks/{id}/invite` | `{role}` → membership with `invite_open: true`. Re-opening reuses the row. |
| `DELETE` | `/decks/{id}/invite` | Closes the link. **Members already in stay in** — revoking a link is not dissolving a class. |
| `POST` | `/decks/join` | `{code}` → `{"deck_id": "<uuid>"}`. Idempotent: joining twice is 200 with the same deck, not 409. |
| `GET` | `/decks/{id}/roster` | `{"members": [DeckMember…]}` with `progress` filled — see below. |

`DeckMembership` (see `lib/models/deck_member.dart` for the authoritative
parser):

```json
{
  "deck_id": "…",
  "my_role": "owner",
  "invite_code": "…",
  "invite_role": "viewer",
  "invite_open": true,
  "members": [
    {"username": "ali", "name": "Ali", "role": "owner",
     "joined_at": "2026-08-01T10:00:00Z", "is_me": true,
     "progress": {"seen": 40, "learning": 22, "mastered": 12,
                  "reviewed_this_week": 31, "mastered_this_week": 3,
                  "last_active_at": "2026-08-06T08:10:00Z"}}
  ]
}
```

`progress` may be `null` on the membership endpoint (the client tolerates it —
"the roster is cheap, the progress is not"); the roster endpoint must fill it.

### Invite codes are bearer credentials

The client's local stand-in derives a 6-char code from `deckId.hashCode`, which
is trivially enumerable. **Do not copy that.** Generate with a CSPRNG,
≥ 64 bits of entropy (e.g. 13 chars of Crockford base32), unique-indexed,
optionally with `expires_at`. Rate-limit `POST /decks/join` per user *and* per
IP: it is the one endpoint where guessing wins access to someone's data.

The link the client renders is `https://app.vocably.ir/join/<code>`
(`kInviteLinkBase`). **Known gap on the client side:** nothing yet parses that
URL into a `joinByCode` call, so the flow is copy-code-and-paste until the PWA
gets a `/join/:code` route. Build the endpoint anyway; note the gap in the
client's issue list.

### `MemberProgress` — match these definitions exactly

The client's screens are written against these and nothing else:

- `seen` — words in this deck the member has met at all (`review_count > 0`,
  i.e. a progress row that has been graded).
- `learning` — boxes **1–3**.
- `mastered` — box **5**.
- Box 4 is deliberately in neither bucket. Do not "fix" this; the client's
  `masteryPercent` is `mastered / seen`, measured against what the member has
  had a chance to learn rather than against the whole deck.
- `reviewed_this_week` / `mastered_this_week` — since Monday in the *member's*
  timezone (section 2).
- `last_active_at` — most recent review in this deck.

Weekly figures **must not** be computed by scanning `word_reviews`.
`CLAUDE.md` already forbids user-facing aggregation over that log, and a
roster of 30 students would do it 30 times. Add a rollup:

```
daily_deck_activity(user_id, deck_id, day date,
                    reviews int, mastered int,
                    PRIMARY KEY (user_id, deck_id, day))
  INDEX (deck_id, day)     -- the roster query
```

Incremented on the same UPDATE the grade already issues (`ON CONFLICT … DO
UPDATE SET reviews = reviews + 1`). `mastered` increments only on the
transition into box 5 — `Word.apply_review` already knows when
`mastered_at` is first set. `seen`/`learning`/`mastered` totals come from
`word_progress` directly (a grouped count, one query for the whole roster —
watch for the N+1 here, it is the obvious way to write this wrong).

Backfill `daily_deck_activity` from `word_reviews` in the migration — the log
has been kept since before the features that consume it, which is exactly what
it was for. Existing users then arrive on the new screens with real history
instead of zeros.

---

## 5. Deck discovery: Explore and person-to-person shares

Distinct from section 4, and the difference is load-bearing:
**saving from Explore takes a copy; sharing with a person shares the deck.**
A published deck is a starting point, and editing your copy must not change
anyone else's.

### Schema (`decks`)

`is_public bool default false`, `published_at timestamptz null`,
`category varchar(32)`, `description text`, `description_fa text`,
`is_official bool default false`, `save_count int default 0`.

### Endpoints

| Method | Path | Notes |
|---|---|---|
| `GET` | `/decks/public?category=&q=` | `[PublicDeck]`. Paginate (the client currently doesn't, so return a sane cap and add `limit`/`offset` for it to adopt). |
| `POST` | `/decks/public/{id}/import` | **Copies** the deck and its words to the caller, returns the new `Deck`. Increments `save_count`. Units should copy too. |
| `GET` | `/decks/shared` | `[SharedDeck]` — pending person-to-person shares. |
| `POST` | `/decks/shared/{id}/accept` | → the `Deck` |
| `DELETE` | `/decks/shared/{id}` | Decline. |
| `POST` | `/decks/{id}/share` | `{to_username}` |

`PublicDeck` keys: `id, name, hue, word_count, author_name, author_username,
is_official, category, description, description_fa, saves`.
`SharedDeck` keys: `id, name, hue, word_count, from_name, from_username,
shared_at, accepted`. (`lib/models/shared_deck.dart`.)

Publishing someone else's shared deck must be impossible (owner only). A public
deck listing exposes `author_username` — that is intentional and is the only
place a handle is published, so make sure a *private* deck's owner handle never
leaks through the same serialiser.

There is no moderation surface here yet. Before `is_public` can be set by
ordinary users in production you need at minimum a report path and an admin
view — flag it, and consider shipping with `is_public` writable by admins only
(`is_official` decks) while the client's Explore tab reads the same endpoint.

---

## 6. Friends

Deliberately small. The client's need is "don't make me type a handle every
time I share", so this is a recency list, not a social graph.

```
friend_links(user_id uuid, friend_user_id uuid,
             last_shared_at timestamptz null,
             created_at, PRIMARY KEY (user_id, friend_user_id))
```

| Method | Path | Notes |
|---|---|---|
| `GET` | `/users/me/friends` | `{"friends": [{username, name, last_shared_at}]}`, most-recently-shared first |
| `POST` | `/users/me/friends` | `{username}`. 422 on unknown handle or own handle, 409 on duplicate. |
| `DELETE` | `/users/me/friends/{username}` | 204 |

Also upsert a link with a fresh `last_shared_at` whenever a share succeeds
(`POST /decks/{id}/share`, `POST /decks/{id}/members`) — the client calls that
`noteShared`.

Two things to decide deliberately, because this is where a small feature
becomes a privacy surface:

- **It is one-directional and needs no consent**, since it reveals nothing the
  sharer did not already know (they typed the handle). Do not turn it into a
  mutual "friendship" without adding an accept step.
- **Handle lookup must be exact-match only.** No prefix search, no
  "find people" endpoint, and the same rate limit as
  `username-available`. Adding a search endpoint later is a product decision
  with a consent question attached, not a convenience.

Adding a friend must not leak anything beyond `name` — no phone, no email, no
stats.

---

## 7. XP and levels

The client currently keeps XP on the device (`vocably-xp`), which means it does
not travel between the phone and the PWA. Move it server-side.

### Schema

```
xp_events(id uuid pk, user_id uuid fk→users ON DELETE CASCADE,
          action varchar(32) not null, points smallint not null,
          occurred_at timestamptz not null,
          ref_type varchar(16) null, ref_id uuid null,
          created_at)
  INDEX (user_id, occurred_at)
users.xp int default 0
```

Same hybrid pattern this repo already uses for review history: an append-only
ledger plus a counter updated in place. The counter is what every request
reads; the ledger is what makes "why is my level 7" answerable and lets the
award rules change without rewriting history.

### The award table (from `lib/models/progress_rewards.dart` — keep in step)

| Action | Points | When |
|---|---|---|
| `grade_word` | 6 | Any card answered in a review session |
| `drill_correct` | 8 | Practice drill, right |
| `drill_wrong` | 3 | Practice drill, wrong — turning up to be tested on your weakest words is the behaviour worth rewarding |
| `add_word` | 5 | A card added by hand or from the reader |
| `finish_session` | 15 | Session completed, on top of the cards |
| `daily_goal` | 25 | Daily goal met, **once per day** |

Levels: `total_xp_for(level) = 50 * level * (level - 1)` — level 2 at 100,
level 3 at 300, level 4 at 600. A formula, not a table, so there is no ceiling
to maintain. Put it in `app/domain/services/` and mirror the client's
`XpLevel` exactly; a disagreement here shows up as a level that changes when
you switch device.

### Where XP is awarded

Server-side, as a side effect of the actions that already have endpoints —
`POST /study/words/{id}/grade`, `POST /words`, and a session-completion signal.
**Never trust a client-supplied point total**; an endpoint that accepts
"give me N XP" is an endpoint that hands out N XP.

The drill and the session-finish need a small amount of new surface. Simplest
honest option: `POST /study/sessions/{id}/complete`, or extend the grade body
with a `source` (`"session" | "drill"`) plus a `session_finished` flag, and
derive `daily_goal` server-side from `daily_deck_activity` rather than trusting
a claim. Pick one, write it down, and tell the client which.

`daily_goal` must be idempotent per day per user — enforce it with a partial
unique index on `(user_id, action, day)` for that action, not with an
application-level check.

### Backfill

Compute each existing user's XP from `word_reviews` (`6 × review count` is a
faithful approximation of `grade_word`; skip the session and goal bonuses,
which are not reconstructible) and write both the ledger rows — or one
summarising row per user, with `action = 'backfill'` — and the counter. Do not
start existing users at zero: they have earned it, and a profile that resets
to level 1 on the day XP became a server feature reads as data loss.

### Response

`GET /users/me` gains `xp`. The client derives level and progress from it, so
that single integer is the whole contract. Optionally also expose
`GET /users/me/xp?days=30` for a history chart — not needed by this branch.

---

## 8. Badges: derive, don't store

The mastery badges (`first` 1, `novice` 10, `apprentice` 50, `scholar` 150,
`linguist` 400, `polyglot` 1000) are a pure function of the number of words the
user has mastered — box 5 across all decks. The client already computes them
from that count.

**Add no table and no endpoint.** Just make sure a mastered-word count is
available: add `mastered_count` to `GET /study/overview` if it isn't already
derivable there (it currently reports `learned_count`, which is not the same
thing — check its definition and either reuse it or add the field alongside).

Storing "earned badges" would only be worth it if you wanted the *timestamp* a
badge was earned, for a notification. That is a later feature; a derived badge
cannot go stale or disagree with the words.

---

## 9. `reviewed_today` — a decision to make, not a feature to guess

The client counts today's reviews **on the device** because `/study/overview`
reports what is *left*, not what has been done. The documented consequence is
that reviewing on the phone does not fill the ring in the PWA.

`daily_deck_activity` from section 4 makes the server side of this free:
`SUM(reviews) WHERE user_id = … AND day = <today in the user's timezone>`.

So add `reviewed_today` to `GET /study/overview`. Note for the client team:
adopting it should keep the local counter as an offline fallback and take the
larger of the two, since a grade that failed to reach the server is still work
the learner did — that reasoning is already in the client's `CLAUDE.md` and
should not be thrown away for cross-device parity.

---

## 10. Explicitly out of scope

State these in a commit message or `CLAUDE.md` so the next person doesn't
wonder:

- **Library / books.** The graded texts are seeded in the client
  (`lib/models/book.dart`) and personal files stay on the device on purpose —
  putting somebody's book on a server is a different decision, with different
  consent. `RestLibraryService` calls `/library/shelves` and
  `/library/books/{id}/text`, which can stay unimplemented; the client uses its
  local implementation. Do not build these without a product decision about
  hosting user documents.
- **Practice activities** beyond the missed-words drill — the drill runs
  entirely on existing endpoints (words in boxes 1–2, distractors from the
  learner's own meanings, graded through `POST /study/words/{id}/grade`).
- **Challenges** beyond the weekly ranking. The client's "challenge" is
  `rankForWeek()` over the roster's weekly numbers; there is no separate
  challenge object, and inventing one server-side would have no UI.

---

## 11. Cross-cutting requirements

- **Authorization tests are the deliverable, not an extra.** For every new
  route, a test that a viewer is refused, a non-member gets 404 (not 403 — do
  not confirm a deck exists to someone who cannot see it), and an
  unauthenticated call gets 401.
- **No N+1.** The roster, the words list with progress, and the deck list with
  stats are the three places this will happen. Aggregate in SQL, as this repo
  already does for the admin surface.
- **Pagination** on `/decks/public` and `/words` (additively:
  `limit`/`offset` with a default cap; the client sends neither today and must
  keep working).
- **Rate limits** on `username-available`, `/decks/join`, `/users/me/friends`,
  and `/decks/{id}/share`.
- **Error contract**: keep `app/api/errors.py`'s existing shape. The client
  surfaces `ApiException.message` directly to users in several places
  (`Enter a handle first`, `They already have this deck`), so 4xx messages are
  user-visible copy — write them as such, and keep them in English (the client
  localises its own strings and passes server messages through).
- **OpenAPI stays the contract surface.** Everything here must show up
  correctly at `/docs`; that is what the client is written against.
- Update `CLAUDE.md` in the same branch: the `words`/`word_progress` split, the
  deck-membership authorization rule, the units cascade, the day/week timezone
  decision, and the account-deletion policy. Each is a thing a future change
  can silently break.

## 12. Suggested commit sequence

Each leaves the tree green:

1. `refactor(db)` — introduce the calendar/timezone helper and `users.timezone`.
2. `feat(words)!` — the `words` / `word_progress` split, migration, backfill,
   and the compatibility tests. Breaking internally, not on the wire.
3. `feat(users)` — `username` + profile fields + backfill + availability
   endpoint.
4. `feat(decks)` — `deck_units` + `words.unit_id` + the four unit endpoints.
5. `refactor(api)` — the `require_deck_role` dependency, applied to every
   existing deck/word route with no behaviour change (owner-only still).
6. `feat(decks)` — `deck_members` + `deck_invites`, the membership/invite/join
   endpoints, and the role gates opening up.
7. `feat(study)` — `daily_deck_activity`, its backfill from `word_reviews`, the
   roster endpoint, `reviewed_today`, `mastered_count`.
8. `feat(decks)` — public decks + person-to-person share/accept/decline.
9. `feat(users)` — friend links.
10. `feat(study)` — XP ledger, counter, awards, backfill.
11. `docs` — `CLAUDE.md` + a `docs/design-v10-contract.md` pinning the wire
    shapes, in the spirit of the existing `ai-card-magic-contract.md`.

## 13. Definition of done

- Two accounts, one deck: an editor's added word appears for the viewer;
  their boxes are independent; the owner sees both members' numbers, the viewer
  sees only their own plus a rank.
- A teacher opens a link, three students join at `viewer`, and the teacher's
  roster shows each one's `seen`/`learning`/`mastered` and this week's activity.
- A unit is deleted; its cards are still there, in no unit.
- An account created before this branch: keeps every box, gets a handle, gets
  XP proportional to their real review history, and their decks show them as
  owner.
- An APK built from the client's `main` (pre-v10) still works against this
  backend, unchanged.
