# Pre-built deck generation & the shared lexicon

How Vocably builds *504 Essential Words*, *1100 Words You Need to Know*, TOEFL/IELTS
lists, phrasal verbs and every future collection from one pipeline — and where the
vocabulary knowledge those decks consume actually lives.

The governing principle:

> Generate a word's knowledge once, persist **every** useful sense (1–4), make it
> reusable by the whole application. A pre-built deck **references** that shared
> knowledge and selects one sense for presentation; it never generates or stores an
> isolated one-sense version of the word.

---

## 1. Recommended architecture

Three layers, two of which already exist. Nothing new is introduced at the
infrastructure level: Postgres, Redis, Celery, all already running.

```
┌─ Layer 3 ─ Build pipeline (NEW) ────────────────────────────────────┐
│  content/decks/<slug>/deck.yaml   ← template, in git, reviewed in PR │
│  deck_build_jobs / deck_build_items ← the resumable plan             │
│  DeckBuildService  →  materialises decks/deck_units/words            │
└──────────────────────────────┬───────────────────────────────────────┘
                               │ consumes
┌─ Layer 2 ─ Lexicon (NEW, durable) ──────────────────────────────────┐
│  lexemes ─< lexeme_senses ─< lexeme_sense_translations               │
│  term-shaped · stable ids · reviewable · versioned · never swept     │
└──────────────────────────────┬───────────────────────────────────────┘
                               │ fed by / reads through
┌─ Layer 1 ─ Existing AI lookup pipeline (UNCHANGED in shape) ────────┐
│  AIStudioService                                                     │
│    └─ CachingAIService      ai_lookup_entries/aliases · disposable   │
│        └─ LexiconAIService  (NEW decorator: read + write-through)    │
│            └─ GroundedAIService   dictionary + targeted translation  │
│                └─ AvalAI / Anthropic / Stub                          │
└──────────────────────────────────────────────────────────────────────┘
```

The one new moving part in the request path is `LexiconAIService`, a decorator over
the `AIService` port — exactly the pattern `CachingAIService` and `GroundedAIService`
already use, so `AIStudioService` and every router stay unaware it exists.

**The build pipeline calls the same `AIService` chain a user lookup calls.** It does
not get a private path to the provider. That is what makes "a word a user looked up
last month costs the deck builder nothing" true by construction rather than by
discipline.

---

## 2. Why this architecture

### Why not just use `ai_lookup_entries` as the shared store

It is tempting — it already holds every sense for a term, impersonally, in Postgres.
It is the wrong shape for three concrete reasons, all of which are load-bearing
properties of the existing design that must not be broken:

| Property of the cache | Why it blocks deck use |
|---|---|
| Key includes `prompt_version` | A prompt bump retires the whole corpus by design (`CLAUDE.md`: "bumping it retires every card the old prompt wrote"). A deck that pinned a cached sense would break on every prompt edit. |
| Key includes `native_language` + `age_bucket` | The English half of a sense (definition, example, POS, context) is re-bought per language. Adding a second native language re-buys the entire corpus. |
| "Never load-bearing" — read failures are misses, rows are swept | Deck content must be durable and referenceable. Making the cache load-bearing would invalidate every "best-effort" shortcut in `CachingAIService` and `lookup_cache_payload.decode`. |
| Payload is an opaque JSON blob | No sense has an id. Nothing can point at "sense 2 of *run*". |

So: **keep the cache exactly as it is** — request-shaped, disposable, keyed by the
effective prompt version — and add a second, durable, term-shaped store beside it.
The two answer different questions:

* Cache: *"has this exact request been answered under this exact prompt?"* — absorbs
  typos, sentences, aliases, and costs one indexed read.
* Lexicon: *"what do we know about this word?"* — survives prompt bumps, has stable
  per-sense ids, carries review status, and is normalised so a new native language
  re-buys only the translation.

### Why the lexicon sits *inside* the cache decorator

```
CachingAIService     ← outermost: cheapest possible answer, absorbs aliases
  └─ LexiconAIService ← durable knowledge, survives a prompt bump
      └─ GroundedAIService
```

A cache hit costs one read and never touches the lexicon. A cache **miss** — which is
what every request becomes the day `PROMPT_VERSION` is bumped — now hits the lexicon
instead of the provider. That single ordering decision is the difference between a
prompt edit costing a full corpus re-buy and costing nothing.

### Why Postgres for the lexicon (§7 answered explicitly)

| Option | Verdict |
|---|---|
| **Postgres, dedicated tables** | **Recommended.** Needs unique constraints for dedup, FKs from deck words for provenance, joins for the admin review queue, and durability. All four are Postgres's job. |
| Redis | Rejected as the store. No constraints, no joins, and durability is a configuration you can lose. Redis keeps the two jobs it is already good at here: the dictionary entry cache and the single-flight generation lock. |
| Postgres + Redis | **This is what you get** — but Redis holds nothing authoritative. |
| Reuse `ai_lookup_entries` | Rejected above. |
| Separate content service / vector DB | Rejected. No requirement here needs a second deployable or ANN search. Sense selection is deterministic (§8); if AI ranking is ever enabled it is one batched provider call, not an index. |

---

## 3. Shared vocabulary content vs deck content

```
lexemes("run")
  ├── lexeme_senses  id=S1  verb   context="Movement"    definition… example…
  ├── lexeme_senses  id=S2  verb   context="Management"  definition… example…
  └── lexeme_senses  id=S3  noun   context="Period"      definition… example…
        └── lexeme_sense_translations (S2, fa) native_meaning="اداره کردن"
                                      (S2, ar) native_meaning="…"

words (a card in a deck)
  ├── lexeme_sense_id = S2      ← provenance / refresh handle
  ├── term "run"  meaning "اداره کردن"  definition …  example …  sense_label "Management"
  └── deck_id, unit_id, created_by_user_id
```

**A deck card is a materialised copy of one sense, not a live view of it.** This is a
deliberate trade-off:

*Against a copy:* the same sense text is duplicated per deck; a lexicon correction does
not propagate automatically.

*For a copy — decisive:* `words` is what the entire application already reads. Study
sessions, `word_progress` (PK `(user_id, word_id)`), `word_reviews`, Explore's
`copy_deck_to`, learner edits, phonetic backfill. Making cards a view over the lexicon
means rewriting all of it, and it breaks a rule the product depends on: **a learner may
edit their card**, and `CLAUDE.md` is explicit that a learner's wording must never flow
back into shared content. A copy makes that structurally impossible.

The nullable `words.lexeme_sense_id` gives back the one thing the copy loses: an
explicit, opt-in `refresh_deck_from_lexicon` job can push a corrected sense into cards
that a learner has not edited. Correction becomes a decision, not a side effect.

**User lookup is unaffected and still returns every sense** (§11): `LexiconAIService`
reconstructs a full `LookupResult` with all 1–4 senses. Only the deck builder collapses
to one.

---

## 4. End-to-end workflow

```
 1. Write content/decks/504-essential-words/deck.yaml + words.yaml   (git, PR-reviewed)
 2. make deck-validate slug=504-essential-words      → structural checks, no DB, no AI
 3. make deck-plan slug=504-essential-words          → creates deck (is_public=false)
                                                       + deck_build_job + N items
 4. make deck-build job=<id>                         → enqueues vocably.ai.build_deck
       ├─ claim batch of PENDING items (conditional UPDATE)
       ├─ per item: normalize → resolve against lexicon
       │     hit  → reuse ALL senses, no AI call
       │     miss → single-flight → AIService chain → validate → store 1–4 senses
       │     hit but required sense absent → enrichment call (bounded, once per item)
       ├─ select the deck's sense (deterministic strategy chain, §8)
       ├─ materialise the words row + unit
       └─ re-enqueue itself while work remains
 5. Job → READY_FOR_REVIEW (or PARTIAL if items failed)
 6. Admin reviews low-confidence selections + NEEDS_REVIEW senses
 7. PATCH /admin/decks/{id}/publish     ← the endpoint that already exists
 8. Visible in Explore. Learners save it; deck_members.self_paced=true means the
    504 cards wait to be started rather than flooding a review queue.
```

Steps 2 and 3 are separable on purpose: validation is free and offline, planning
writes rows, building spends money.

---

## 5. Database model

### 5.1 Lexicon

```sql
CREATE TABLE lexemes (
    id              uuid PRIMARY KEY,
    -- normalize_lookup_input() applied; the dedup key
    lemma           varchar(255) NOT NULL,
    language        varchar(16)  NOT NULL DEFAULT 'en',
    -- provider/dictionary casing, what a learner sees
    display_term    varchar(255) NOT NULL,
    phonetic        varchar(200),          -- NULL = unknown, '' = has none (existing rule)
    created_at      timestamptz NOT NULL,
    updated_at      timestamptz NOT NULL,
    UNIQUE (language, lemma)               -- the concurrency primitive, §11
);

CREATE TABLE lexeme_senses (
    id              uuid PRIMARY KEY,
    lexeme_id       uuid NOT NULL REFERENCES lexemes(id) ON DELETE CASCADE,
    -- deterministic identity: slug(part_of_speech) || ':' || slug(context)
    sense_key       varchar(80) NOT NULL,
    register        varchar(16) NOT NULL DEFAULT 'adult',   -- child|teen|adult
    position        smallint    NOT NULL,                   -- 0-based, provider order
    part_of_speech  varchar(32) NOT NULL,
    context         varchar(120) NOT NULL,   -- the chip: "Movement", "Management"
    definition      text        NOT NULL,    -- English, learner-dictionary register
    example         text        NOT NULL DEFAULT '',
    status          varchar(16) NOT NULL DEFAULT 'auto',    -- auto|needs_review|approved|rejected
    content_version integer     NOT NULL,    -- deps._effective_prompt_version() at write
    provider        varchar(32) NOT NULL DEFAULT '',
    model           varchar(128) NOT NULL DEFAULT '',
    source          varchar(16) NOT NULL,    -- lookup|deck_build|enrichment|manual
    created_at      timestamptz NOT NULL,
    updated_at      timestamptz NOT NULL,
    UNIQUE (lexeme_id, sense_key, register)  -- idempotent enrichment, §6
);
CREATE INDEX ix_lexeme_senses_lexeme_pos ON lexeme_senses (lexeme_id, position);
CREATE INDEX ix_lexeme_senses_status     ON lexeme_senses (status)
       WHERE status IN ('needs_review','rejected');
CREATE INDEX ix_lexeme_senses_version    ON lexeme_senses (content_version);

CREATE TABLE lexeme_sense_translations (
    id              uuid PRIMARY KEY,
    sense_id        uuid NOT NULL REFERENCES lexeme_senses(id) ON DELETE CASCADE,
    native_language varchar(64) NOT NULL,
    native_meaning  text NOT NULL,
    status          varchar(16) NOT NULL DEFAULT 'auto',
    content_version integer NOT NULL,
    created_at      timestamptz NOT NULL,
    updated_at      timestamptz NOT NULL,
    UNIQUE (sense_id, native_language)
);
```

**No `user_id` anywhere in these three tables**, same rule as the lookup cache. This is
platform knowledge, not user data. A learner's card edits never flow back here.

Why translations are split out: the English half of a sense (definition, example, POS,
context) is language-neutral and is by far the more expensive half to get right — the
grounded path takes it from the dictionary verbatim. `native_meaning` is a short
headline. Splitting means adding Arabic costs a translation pass over existing senses,
not a full regeneration; and one bad Persian gloss can be re-generated without touching
the English a human already approved.

Why `sense_key` is derived from `(part_of_speech, context)` rather than being a random
id: enrichment must be able to answer *"do we already have this sense?"* without an AI
call and without a fuzzy match. Two senses that agree on POS and context label are the
same sense for card purposes.

Why max 4 is not a DB constraint: it is enforced in `LexiconWriter` (application layer),
because the rule is "the card deck renders at most 4" — a product cap, already stated as
`MAX_LOOKUP_SUGGESTIONS`, not a data invariant. A partial unique index enforcing it
would fail an enrichment write in a way nothing could recover from.

### 5.2 Deck cards

One additive column, nullable:

```sql
ALTER TABLE words ADD COLUMN lexeme_sense_id uuid
    REFERENCES lexeme_senses(id) ON DELETE SET NULL;
CREATE INDEX ix_words_lexeme_sense ON words (lexeme_sense_id);
```

`SET NULL`, matching `created_by_user_id`: provenance is exactly what may be lost, and a
card must survive its source sense being deleted. Hand-typed cards keep it NULL forever
and nothing changes for them.

### 5.3 Build jobs

```sql
CREATE TABLE deck_build_jobs (
    id                uuid PRIMARY KEY,
    template_slug     varchar(80)  NOT NULL,
    template_version  varchar(32)  NOT NULL,
    template_hash     varchar(64)  NOT NULL,   -- sha256 of the resolved template files
    deck_id           uuid REFERENCES decks(id) ON DELETE SET NULL,
    state             varchar(24)  NOT NULL,
    -- pinned for the whole run so a mid-build deploy cannot split the deck
    content_version   integer      NOT NULL,
    native_language   varchar(64)  NOT NULL DEFAULT 'Persian',
    register          varchar(16)  NOT NULL DEFAULT 'adult',
    -- counters, incremented in SQL, never read-modify-write
    items_total       integer NOT NULL DEFAULT 0,
    items_done        integer NOT NULL DEFAULT 0,
    items_failed      integer NOT NULL DEFAULT 0,
    lexemes_reused    integer NOT NULL DEFAULT 0,
    lexemes_generated integer NOT NULL DEFAULT 0,
    senses_enriched   integer NOT NULL DEFAULT 0,
    ai_calls          integer NOT NULL DEFAULT 0,
    created_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
    started_at        timestamptz, finished_at timestamptz,
    last_error        text,
    created_at        timestamptz NOT NULL, updated_at timestamptz NOT NULL,
    UNIQUE (template_slug, template_hash, state) WHERE state = 'generating'  -- one live build per template
);

CREATE TABLE deck_build_items (
    id             uuid PRIMARY KEY,
    job_id         uuid NOT NULL REFERENCES deck_build_jobs(id) ON DELETE CASCADE,
    position       integer NOT NULL,        -- the book's order, authoritative
    unit_label     varchar(40) NOT NULL DEFAULT '',
    unit_position  smallint NOT NULL DEFAULT 0,
    source_term    varchar(255) NOT NULL,   -- exactly as the template wrote it
    normalized     varchar(255) NOT NULL,
    sense_hint     jsonb,                   -- {pos, context, gloss} from the template
    state          varchar(16) NOT NULL DEFAULT 'pending',
    lexeme_id      uuid REFERENCES lexemes(id) ON DELETE SET NULL,
    sense_id       uuid REFERENCES lexeme_senses(id) ON DELETE SET NULL,
    word_id        uuid REFERENCES words(id) ON DELETE SET NULL,
    selection      varchar(24),             -- explicit|hint|category|first|ai|manual
    selection_score real,
    attempts       smallint NOT NULL DEFAULT 0,
    next_attempt_at timestamptz,
    last_error     text,
    enriched       boolean NOT NULL DEFAULT false,  -- at most one enrichment, ever
    updated_at     timestamptz NOT NULL,
    UNIQUE (job_id, position)               -- resumability + dedup in one constraint
);
CREATE INDEX ix_deck_build_items_claim ON deck_build_items (job_id, state, next_attempt_at);
```

`UNIQUE (job_id, position)` is what makes a redelivered Celery message harmless: the
plan is fixed at plan time, and building is a state transition on existing rows, never
an insert.

Item states: `pending → resolving → resolved → done`, plus `failed` and
`needs_review`. `resolving` carries a `claimed_at`; a row stuck in it past a timeout is
reclaimed (§12).

---

## 6. The sense model

Answering §2/§3 directly against the existing code: `MeaningSuggestion` is *already* the
right per-sense shape, and `LookupResult.suggestions` is already a list of 1–4. The
lexicon does not invent a model — it gives the existing one durable rows and ids.

| `MeaningSuggestion` field | Lexicon home | Language |
|---|---|---|
| `part_of_speech` | `lexeme_senses.part_of_speech` | English |
| `context` | `lexeme_senses.context` | English |
| `definition` | `lexeme_senses.definition` | English |
| `example` | `lexeme_senses.example` | the term's language |
| `native_meaning` | `lexeme_sense_translations.native_meaning` | learner's |
| `LookupResult.phonetic` | `lexemes.phonetic` (per headword, not per sense) | IPA |

`phonetic` stays on the lexeme, not the sense, because the senses of a headword share a
pronunciation — the existing `LookupResult` already models it that way, and the
NULL-vs-`""` distinction (`NULL` = no answer yet, `""` = the dictionary says there is
none) is carried over unchanged so the backfill task's semantics still hold.

Multiple examples per sense are deliberately **not** modelled yet. Nothing renders a
second one; `CLAUDE.md` records that multi-`examples` was already dropped once for that
reason. When a screen needs it, add `lexeme_sense_examples` — the sense id is already
stable, so it is an additive migration.

---

## 7. Deck templates

**Templates live in git as files, not as database rows.**

```
content/decks/504-essential-words/
    deck.yaml      # metadata, source citation, structure, presentation rules
    words.yaml     # the ordered list, grouped by lesson
```

```yaml
# deck.yaml
slug: 504-essential-words
version: "1.0.0"
name: 504 Essential Words
name_fa: ۵۰۴ واژه ضروری
category: exam
description: The classic 504-word vocabulary course, one lesson at a time.
description_fa: …
official: true
hue: 262

source:
  title: 504 Absolutely Essential Words
  authors: [Bromberg, Liebb, Traiger]
  edition: 6th
  isbn: "978-1438074450"
  url: https://…
  rights: |
    Headword list and lesson boundaries only, used as an index of study order.
    No definitions, examples or exercises are reproduced. All card content on
    this deck is generated by Vocably and is our own.

structure:
  unit_naming: "Lesson {n}"          # deck_units.name
  words_per_unit: 12                 # asserted, not computed — a mismatch fails validation
  expected_units: 42
  expected_words: 504

generation:
  native_language: Persian
  register: adult
  sense_selection: [explicit, hint, category, first]   # the chain, in order, §8
  category_bias: [general]
  enrichment: allowed                # allowed | forbidden
  max_senses: 4

publication:
  review_required: true
  publish: manual                    # never auto-publish
```

```yaml
# words.yaml
units:
  - name: Lesson 1
    words:
      - abandon
      - keen
      - jealous
      - tact
      - { term: run, sense: { pos: verb, context: Management } }   # explicit pin
      - { term: bound, hint: "obliged or required to do something" }
```

### Why files rather than DB-managed templates

* **Reviewable.** A new deck's word list arrives as a pull request. Ordering, lesson
  boundaries and sense pins get read by a human before a single token is spent.
* **Deterministic and diffable.** `template_hash` on the job records exactly what was
  built; a later diff shows precisely what changed.
* **The order is the product.** For reference decks the sequence *is* the value, and it
  must never be something the AI, or an admin's drag-and-drop, can silently reorder.
* **No editor to build.** A DB-first design needs a template CRUD UI before deck one
  ships.

The job's `deck_build_items` rows *are* the database materialisation of the template —
created once at plan time, frozen for the run. So there is exactly one place structure
can drift from, and it is a git file.

### Copyright and reference decks

The pipeline stores, from a copyrighted source: **the ordered headword list and the
lesson boundaries**, plus citation metadata. It stores **none** of the source's
definitions, example sentences, exercises, or explanatory text — every word of card
content is generated by our own pipeline or taken from a free dictionary.

That separation is also what the validator enforces: `words.yaml` accepts a term, an
optional POS/context pin, and an optional short disambiguating `hint`. There is no field
in which a source's definition could be pasted. Name decks descriptively
("Essential 504 — Vocabulary Course") rather than as the book, and keep the `rights`
note in `deck.yaml` so the reasoning travels with the deck. Word lists and arrangements
sit in genuinely contested territory in some jurisdictions — worth a lawyer's read
before publishing commercially; this design minimises exposure but does not eliminate it.

---

## 8. Sense selection strategy

Deterministic, ordered, first match wins. **No AI call is made to rank senses** unless a
template explicitly opts in.

| # | Strategy | Signal | Recorded as |
|---|---|---|---|
| 1 | **Explicit pin** | `sense: {pos, context}` in the template, or a reviewer's manual choice | `explicit` |
| 2 | **Hint match** | Template `hint` scored against each sense's `definition + context` by normalised token overlap (stopwords removed). Accepted only above `SENSE_HINT_MIN_SCORE` (start at 0.35). | `hint` |
| 3 | **Category prior** | `deck.category` → preferred POS and context keywords, a static table in `sense_selection.py` (`business` → management/finance/commerce; `academic` → analysis/research). Highest-scoring sense above threshold. | `category` |
| 4 | **First sense** | `position = 0`. Not a fallback of last resort but a good prior: both the dictionary path (`DictionaryEntry.top()` ranks senses carrying an example first) and the generative prompt put the most common sense first. | `first` |
| — | **AI ranking** | Opt-in per template. One batched call ranking the senses of up to 20 words at once; never per-word. | `ai` |

Every item stores `selection` and `selection_score`. The review queue sorts by
"strategy = first/category, score below threshold" — that is precisely the set a human
should look at, and it is usually small.

**Nothing is discarded.** Unselected senses remain in the lexicon, served to user
lookups, and available to the next deck that needs a different one. A deck's choice is
one FK.

---

## 9. Content reuse strategy

Resolution order for one term, cheapest first:

```
normalize_lookup_input(term)
  ├─ 1. lexicon: lexeme + senses at this register exist?
  │       └─ yes → does a sense satisfy the deck's requirement? (§8 chain)
  │                 ├─ yes → reuse, no AI call, no provider touch          ← the common case
  │                 └─ no  → enrichment (§ below), once per item
  └─ 2. no lexeme → AIService chain:
          CachingAIService hit  → 0 provider calls, write through to lexicon
          → LexiconAIService    → (already checked, so this is the miss path)
          → GroundedAIService   → dictionary (free, Redis-cached) + one translate call
          → provider            → full generation
        then validate → store 1–4 senses + translations → select → materialise
```

Translation reuse is separate and cheaper: if the senses exist but
`lexeme_sense_translations` has no row for this native language, only a translation call
is made — the English half is never re-bought.

### Enrichment: the existing word missing a required sense (§6 of the brief)

```
lexeme "run": S1 Movement (verb), S2 Management (verb)
deck needs:   the "sequence/period" noun sense
        ↓
  no sense clears the selection threshold, and item.enriched = false
        ↓
  enrichment prompt: term + the senses we ALREADY have + the requirement
       "return only senses not already listed; at most N new"
        ↓
  validate → drop any whose sense_key collides (UNIQUE catches the rest)
        ↓
  append while total < 4, keeping existing positions untouched
        ↓
  re-run selection.  Still nothing? → item state = needs_review, never a loop.
```

Three guards make this safe against cost spirals: `deck_build_items.enriched` allows it
**once per item ever**; the `UNIQUE (lexeme_id, sense_key, register)` constraint makes a
redelivered enrichment a no-op; and `max_senses: 4` caps growth. Enrichment appends —
it never rewrites or reorders existing senses, because a published deck may already
point at one.

---

## 10. Celery / job architecture

```python
TASK_MODULES = [
    "app.tasks.maintenance",
    "app.tasks.phonetics",
    "app.tasks.deck_build",     # NEW
]
```

Tasks (all named `vocably.ai.*` so `task_routes` puts them on the **AI queue** — a
500-word build must never delay partition maintenance):

| Task | Role |
|---|---|
| `vocably.ai.build_deck(job_id)` | The driver. Claims a batch, processes it, re-enqueues itself while work remains. |
| `vocably.ai.refresh_lexicon(...)` | Explicit, bounded regeneration of stale senses (§14). Never scheduled. |
| `vocably.ai.refresh_deck_words(deck_id)` | Explicit push of corrected lexicon content into unedited cards. |

### Why a self-rescheduling batch task, not fan-out

The obvious design — one task per word, gathered by a chord — is **not available here**:
`CELERY_RESULT_BACKEND` is empty by default (`CLAUDE.md`: "nothing reads these tasks'
return values"), and chords require a result backend. Adding one to coordinate a batch
job is real infrastructure cost for a job that has a perfectly good coordination
primitive already: the `deck_build_items` table.

The batch driver also gives, for free:

* **A natural rate limit.** Batch size × worker concurrency bounds in-flight provider
  calls. No token bucket to write.
* **Trivial resumability.** State is rows. A worker dying mid-batch loses at most the
  current item; the next run picks up `pending` and reclaims stale `resolving`.
* **One place counters are updated**, incremented in SQL (`items_done = items_done + 1`)
  — never read-modify-write, the rule `record_grade` already follows.
* **No fan-out storm.** 504 words do not become 504 queued messages competing with
  everything else on the AI queue.

```python
@celery_app.task(name="vocably.ai.build_deck", bind=True,
                 autoretry_for=(Exception,), retry_backoff=60,
                 retry_backoff_max=600, retry_jitter=True, max_retries=5)
def build_deck(self, job_id: str) -> str:
    outcome = run_async(_run_batch(UUID(job_id), settings.deck_build_batch_size))
    if outcome.has_more:
        build_deck.apply_async((job_id,), countdown=settings.deck_build_batch_delay)
    return outcome.summary
```

`run_async` is mandatory, not stylistic — `CLAUDE.md` explains why `asyncio.run` in a
Celery worker poisons the second task's pooled connections.

### Transaction boundaries

**No database transaction is ever held open across a provider call.** Per item:

1. `UPDATE … SET state='resolving', claimed_at=now() WHERE id=… AND state='pending'`
   — commit. (Zero rows updated means another worker has it: skip.)
2. Provider / dictionary calls with no transaction open.
3. One short transaction: upsert lexeme + senses + translations, materialise the `words`
   row, set the item `done`, increment job counters. Commit.

Step 3 is atomic per item, which is the correct granularity: a crash between items costs
one item's work, and re-running that item is a no-op because of step 1's conditional
claim plus the unique constraints.

---

## 11. Idempotency & duplicate prevention

The scenario in §13 of the brief — a user lookup and the deck builder racing on
"abandon" — has a two-tier answer.

**Tier 1: correctness — database unique constraints. Non-negotiable.**

* `lexemes UNIQUE (language, lemma)` — `INSERT … ON CONFLICT DO NOTHING RETURNING id`,
  then re-select on conflict.
* `lexeme_senses UNIQUE (lexeme_id, sense_key, register)` — `ON CONFLICT DO NOTHING`, so
  a racing writer's duplicate sense vanishes silently rather than doubling the deck.
* `lexeme_sense_translations UNIQUE (sense_id, native_language)`.
* `deck_build_items UNIQUE (job_id, position)` + the conditional claim.

`DO NOTHING`, never `DO UPDATE` — the same reasoning as `add_if_absent` for memberships:
the second writer must not overwrite what the first (possibly human-approved) wrote.
Worst case for a lost race: **one duplicated AI call**, no corrupt data.

**Tier 2: cost — Redis single-flight, best-effort.**

```python
# key: lexgen:{language}:{lemma}:{register}:{content_version}
if await redis.set(key, worker_id, nx=True, ex=90):
    ... generate ...          # we own it
else:
    await asyncio.sleep(0.25) # brief poll, bounded (~3s total)
    if hit := await lexicon.get(...): return hit
    ... generate anyway ...   # never block a user on someone else's call
```

An unreachable Redis degrades to "both callers generate" — never to a hang, never to an
error. Same posture as every other Redis use in this codebase.

**Explicitly rejected: `pg_advisory_xact_lock`.** It would hold a database connection
open for the entire multi-second provider call. Under a 500-word build plus normal
traffic that exhausts the pool, and a slow provider becomes a site-wide outage. The lock
must live outside the database precisely because the thing being serialised is slow.

**Also rejected: a `generating` status column with polling.** It is a lock with no
lease, and a worker that dies leaves a word permanently un-generatable until someone
notices. Redis TTLs expire on their own.

---

## 12. Failure, retry, resume

**Failure is per item, never per job.** One dead word must not stop 503 others.

| Failure | Handling |
|---|---|
| Provider timeout / 5xx / rate limit | Item `attempts += 1`, `next_attempt_at = now + 2^attempts × 60s ± jitter`, state back to `pending`. Max 3 → `failed`. |
| Invalid AI response (schema/validation) | One immediate retry inside the adapter (already exists), then one item-level retry with a stricter prompt, then `failed`. |
| Dictionary miss/outage | Not a failure. Generative path answers, as today. |
| Worker crash mid-item | Item stuck in `resolving`; reclaimed after `DECK_BUILD_CLAIM_TIMEOUT` (default 10 min) by the next batch. |
| Worker crash mid-batch | `acks_late` redelivers; conditional claims make it a no-op for finished items. |
| DB failure during step 3 | Transaction rolls back; the item returns to `pending` via claim timeout. |
| Job-level exception | Celery `autoretry_for` with backoff; after `max_retries` the job goes `failed` with `last_error` — the items keep their individual state, so a manual re-enqueue resumes. |

**Resume is a single command** and needs no special "resume mode":

```bash
make deck-build job=<id>     # picks up pending + reclaimable + retry-due items
```

Because the plan is rows and every step is a conditional state transition, re-running a
completed job is a no-op, and re-running a job that died at word 736 processes exactly
the remaining 264 (plus any that are retry-due).

**Rate limiting** is `batch_size × worker concurrency`, plus a `countdown` between
batches. Two knobs, no algorithm. If a provider starts 429ing, the item-level backoff
already spreads the retries.

---

## 13. AI cost optimisation

Cost is designed in, at five levels:

1. **The dictionary first.** `GroundedAIService` already replaces "recall every sense of
   *run*" with "pick from these 12 and write a Persian headline" — fewer output tokens
   and a far cheaper failure mode. Free, 27 ms, Redis-cached across all learners.
2. **The lookup cache.** Unchanged; absorbs repeats within a prompt version.
3. **The lexicon.** Absorbs repeats *across* prompt versions, across native languages
   (English half reused), and across decks. This is the layer that makes deck #2 cost a
   fraction of deck #1: IELTS and TOEFL lists overlap heavily with 504 and with each
   other.
4. **Selection without generation.** Choosing a deck's sense costs zero tokens in
   strategies 1–4. Enrichment happens only when selection genuinely fails, once per item.
5. **No implicit regeneration, ever.** A prompt bump costs nothing (§14). Refresh is a
   command with `--limit` and a dry-run.

The number that proves it works is on every job: `lexemes_reused / items_total`. For a
second exam deck built after 504, expect it high; if it is not, the normalisation is
wrong (§17).

Model choice is per-provider config already (`AVALAI_MODEL`, `ANTHROPIC_MODEL`); consider
a cheaper model for the build queue than for interactive lookups — the build is not
latency-sensitive and its output goes through review. That is a config split, not code.

---

## 14. Content versioning

Explicit answers to the brief's questions:

| Question | Answer |
|---|---|
| Should existing content stay reusable after a prompt change? | **Yes**, in the lexicon. The cache still retires (cheap, and it is how a prompt fix reaches learners fast on words nobody has stored). |
| Should a new prompt invalidate old content? | **No.** It marks it stale: `content_version < LEXICON_MIN_CONTENT_VERSION`. Stale content is still served — outdated wording beats a bill for 40,000 regenerations and a spike of provider calls. |
| Should regeneration be explicit? | **Always.** `vocably.ai.refresh_lexicon(min_version, limit, dry_run)`. Never scheduled, never automatic. |
| Should versions coexist? | **One row per `(lexeme, sense_key, register)`.** Refresh updates in place and bumps `content_version`. Full version history is a `lexeme_sense_revisions` table if editorial rollback is ever needed — deliberately deferred, because nothing today reads it. |
| Should a deck pin a version? | It does something stronger: deck cards are **materialised copies**, so a published deck is immune to lexicon churn by construction. `words.lexeme_sense_id` allows an explicit, opt-in refresh. |
| What does a user lookup get? | Whatever the lexicon currently holds, `rejected` senses excluded, `approved` preferred. |

The one hard rule, and the failure mode the brief is right to fear: **a prompt bump must
never trigger regeneration.** It changes a threshold and a report, nothing else.

Within a job, `content_version` is **pinned at plan time**. A deploy that bumps
`PROMPT_VERSION` at word 300 of 504 must not leave the second half of the deck written by
a different prompt than the first.

---

## 15. Quality control

Three gates. Everything below is deterministic — no AI judges AI.

**Gate 1 — schema** (exists): `payloads.py` for the provider's response,
`lookup_cache_payload.decode` for stored blobs. Extend the same way for lexicon rows.

**Gate 2 — `SenseValidator`** (new, `app/domain/services/sense_validation.py`, pure and
unit-testable):

| Rule | Severity |
|---|---|
| `definition` and `native_meaning` non-empty after strip | reject |
| `part_of_speech` in the known set (noun, verb, adjective, adverb, phrase, idiom, …) | reject |
| `context` 1–3 words, ≤ 40 chars | reject |
| No two senses share a `sense_key` | reject the later |
| No two senses share an `example` | reject the later |
| 1 ≤ senses ≤ 4 | truncate at 4; zero → treat as a generation failure |
| `definition` is Latin-script (English) | reject |
| `native_meaning` contains native-script characters when the language expects them (Persian → Arabic block) | reject — catches the model answering in the wrong language |
| Length bounds (definition ≤ 400, example ≤ 300, native_meaning ≤ 200) | reject |
| `example` contains the term or a plausible inflection | **warn** — English morphology makes this unreliable as a hard rule |
| `example` is not a bare restatement of the definition | warn |
| Term appears verbatim in `native_meaning` (untranslated) | warn |

Rejections → one retry → item `failed`. Warnings → stored with
`status = 'needs_review'`, deck build proceeds. Never block a 504-word build on a
questionable example sentence; surface it instead.

**Gate 3 — human review**, per template's `review_required`:

```
auto → needs_review → approved      (approved content survives every prompt bump)
                   ↘ rejected       (never served; a refresh may replace it)
```

The reviewer's queue is not "all 504 cards". It is: items with `selection` in
(`first`, `category`) below threshold, senses with `status = 'needs_review'`, and failed
items. On a good build that is a few dozen rows.

---

## 16. Admin panel

Read-only monitoring plus two write actions (review, publish). All routes take
`CurrentAdmin`, camelCase `serialization_alias` per the existing contract.

```
GET   /admin/deck-builds                    list jobs + progress
GET   /admin/deck-builds/{id}               detail + counters
GET   /admin/deck-builds/{id}/items?state=  the review/failure queue
POST  /admin/deck-builds/{id}/retry         reset failed items → pending, re-enqueue
POST  /admin/deck-builds/{id}/cancel        stop after the current batch
PATCH /admin/deck-build-items/{id}          manual sense override (selection='manual')
PATCH /admin/lexeme-senses/{id}             approve / reject / edit
GET   /admin/lexicon?q=&status=             browse shared content
PATCH /admin/decks/{id}/publish             ← already exists, unchanged
```

The progress screen:

```
504 Essential Words                              GENERATING   ▓▓▓▓▓▓▓░░░  57%

Lessons  25 / 42            Words  287 / 504
Reuse    231 reused · 56 generated · 8 enriched   ← reuse ratio 80%
Senses   just stored: 194   ·   needs review: 11
Failures 3 (2 retrying, 1 exhausted)      Retries 7
AI calls 64      Est. remaining  ~52 calls · ~$0.31 · ~9 min
```

Cost and ETA are estimates from `ai_calls` × a configured `AI_COST_PER_LOOKUP_USD` and
observed throughput — labelled as estimates, never as billing.

Logged/monitored: one structured line per item completion (term hashed or omitted for
user-lookup writes, plain for deck builds — deck terms are not user data), batch
summaries, provider error rates, 429 counts, reuse ratio per job, and an alert if
`items_failed / items_total > 5%`.

---

## 17. Generation vs publishing

**A partially generated deck cannot become visible, structurally**: `decks.is_public`
defaults to false and only `PATCH /admin/decks/{id}/publish` flips it. The build pipeline
never touches it. That is one line of policy and no new mechanism.

Job states:

```
PLANNED ─→ GENERATING ─┬─→ COMPLETED ────→ READY_FOR_REVIEW ─→ APPROVED ─→ PUBLISHED
                       ├─→ PARTIAL  ──────┘ (some items failed; review or retry)
                       ├─→ CANCELLED
                       └─→ FAILED
                                                                      ARCHIVED
```

`PUBLISHED` on the job mirrors the deck flip for audit; the deck's own truth stays
`decks.is_public` + `published_at`, which Explore already reads.

The build deck is owned by a dedicated **system user** (`is_official = true`,
`created_by_user_id` = that user on every card). Two consequences worth stating: the
system user must never be deletable through `DELETE /users/me`, and official decks are
copied out of Explore by `copy_deck_to`, which already preserves `created_at` ordering
and `phonetic` — so the book's lesson order survives the copy.

---

## 18. Trigger: chat vs admin vs CLI vs service

| | Reliability | Simplicity | Ops | Observability | Security | Resumable | New deck types |
|---|---|---|---|---|---|---|---|
| **A. Claude/chat → task** | Low | High | Low | Poor | **Poor** — a chat turn triggering paid, irreversible work with no audit trail | via job | Easy |
| **B. Admin panel** | High | Medium | Low | **Best** | Good — `CurrentAdmin`, audited | via job | Needs a template editor |
| **C. CLI / make target** | High | **Best** | None | Adequate (logs) | Good — shell access required | via job | **Best** — file + PR |
| **D. Separate service** | High | Low | **High** | Good | Good | via job | Easy |
| **E. Hybrid C+B** | High | Medium | Low | **Best** | Good | via job | **Best** |

**Recommendation: E, with C first.**

Templates live in git, so the trigger belongs where git is — a `make` target run by a
developer who just merged the word list. The admin panel then owns what it is genuinely
better at: watching progress, working the review queue, and publishing. Admin gets a
"start build" button in phase 3, once template files have proven stable; it enqueues the
identical Celery task, so there is one implementation and the CLI and the button cannot
drift.

**D is rejected outright** — a separate service duplicates the AI adapters, the lexicon
access and the config, to run a job that is measured in hundreds of provider calls per
month.

**A is rejected as a control plane** and worth being blunt about: a chat message that
spends money and writes content the public will read has no audit trail, no permission
check, and no reproducibility. Claude's genuinely useful role here is upstream — drafting
`words.yaml` from a source, sanity-checking lesson boundaries, opening the PR. The human
merges; the pipeline builds.

---

## 19. Recommended approach for this project, concretely

* Lexicon in **Postgres**, three tables, no `user_id`, additive migration.
* `LexiconAIService` decorator between `CachingAIService` and `GroundedAIService`.
  Behind `LEXICON_ENABLED` (default true once shipped) so it can be taken out of the
  request path in one env var, matching `AI_CACHE_ENABLED`.
* Templates as **YAML in git**; validation offline; plan/build/resume as `make` targets.
* One **self-rescheduling Celery task** on the existing `ai` queue. No result backend, no
  chords, no new broker.
* **Unique constraints for correctness, Redis `SET NX` for cost.** No advisory locks.
* Deck cards stay `words` rows, plus one nullable `lexeme_sense_id`.
* Publish through the endpoint that already exists.

Nothing new to deploy. Two new tables' worth of concepts, one new decorator, one new
task module.

---

## 20. Example: 504 Essential Words

```bash
# 1. Word list lands via PR: content/decks/504-essential-words/{deck,words}.yaml
make deck-validate slug=504-essential-words
#   ✓ 42 units, 504 words, 12 per unit as declared
#   ✓ no duplicate terms (or: "run appears in Lesson 3 and Lesson 27" → decide)
#   ✓ 6 explicit sense pins, 31 hints, 467 unhinted
#   ✓ no source definitions present   (rights check)

# 2. Plan: creates the deck (private) + job + 504 items. No AI, no cost.
make deck-plan slug=504-essential-words
#   job 0f3c… PLANNED · deck 9a1b… (is_public=false) · content_version pinned at 4021

# 3. Build
make deck-build job=0f3c…
```

Batch by batch:

* `abandon` — lexicon hit from user lookups. 3 senses. Category prior picks the
  "give up completely" verb sense, score 0.71. **0 AI calls.**
* `keen` — miss. Grounded path: dictionary gives 6 senses + `/kiːn/`; one translate call
  returns 3 Persian headlines. Validated, stored as 3 senses + 3 translations. First
  sense selected. **1 AI call, 1 free dictionary call.**
* `run` — lexicon hit, 2 senses, template pins `{pos: verb, context: Management}` →
  exact match. **0 AI calls.**
* `tact` — provider 429. Item → `pending`, `next_attempt_at = +2 min`. Batch continues.

Crash at word 736 of a bigger list: `make deck-build job=…` again. 735 items are `done`
and skipped; stale `resolving` rows are reclaimed after 10 minutes; the run continues.

Finish:

```
COMPLETED · 504/504 · reused 231 · generated 265 · enriched 8 · AI calls 273
needs_review: 11 senses, 19 low-confidence selections
```

Admin works the 30-row queue, approves, hits publish. Explore lists it. A learner saves
it and — because `deck_members.self_paced` is set on Explore copies — gets 504 cards
waiting to be started, not 504 cards due today.

---

## 21. Example: a brand-new deck (Business English)

```yaml
slug: business-english-core
name: Business English Core
category: business
structure: { unit_naming: "Module {n}", words_per_unit: 20, expected_units: 15 }
generation:
  native_language: Persian
  sense_selection: [explicit, hint, category, first]
  category_bias: [business]
```

Same three commands. The only differences: `category_bias: [business]` makes strategy 3
prefer management/finance/commerce contexts, and unit naming changes. **No pipeline code
is touched.** Adding *Common Phrasal Verbs* additionally sets `expected_pos: phrase` in
the validator config so `get across` is not flagged as a two-word anomaly.

Because `run`, `board`, `charge` and friends are already in the lexicon from 504, this
deck's reuse ratio starts high — and where it needs the *business* sense of a word whose
stored senses are all general, enrichment adds exactly that sense to the shared lexeme
(§22).

---

## 22. Example: cached word, multiple senses, no AI call

```
Deck item: "abandon" (504, Lesson 1)
  normalize → "abandon"
  lexicon hit: lexeme L-88
      S1  verb  Departure    "to leave someone or something permanently"
      S2  verb  Surrender    "to stop doing something before it is finished"
      S3  noun  Freedom      "complete lack of restraint"
  translations for Persian: present on all three
  selection: category 'exam' → no strong prior → strategy 4 (first) → S1, score n/a
  materialise words row:
      term 'abandon' · meaning 'رها کردن' · definition S1.definition
      example S1.example · sense_label 'Departure' · phonetic from lexeme
      lexeme_sense_id = S1
  AI calls: 0.  Provider calls: 0.  S2 and S3 remain, untouched, still served to lookups.
```

## 23. Example: cached word needing a new sense

```
Deck item: "run" (Business English, Module 4), hint: "to be in charge of a company"
  lexicon hit: lexeme L-12
      S1 verb Movement  "to move quickly on foot"
      S2 noun Period    "a continuous series of performances"
  strategy 2 (hint) scores: S1 0.04 · S2 0.06 → both below 0.35
  strategy 3 (category business) → no context match
  item.enriched = false, template allows enrichment
        ↓
  enrichment call: term + the two existing senses + "the deck needs: to be in charge
  of a company; return only senses not already listed, max 2"
        ↓
  returns  verb / Management  "to control or be in charge of a business"
           verb / Operation   "to cause a machine to work"
        ↓
  validate → sense_keys 'verb:management', 'verb:operation' — no collision
  insert ON CONFLICT DO NOTHING at positions 2, 3 → lexeme now has 4 senses (cap reached)
  item.enriched = true
        ↓
  re-run selection: hint vs 'verb:management' scores 0.62 → selected, strategy 'hint'
  materialise card, lexeme_sense_id = S_management
  AI calls: 1 (enrichment only — the existing senses were not re-bought)
```

S1 and S2 are unchanged and still at positions 0 and 1, so the 504 card pointing at S1
is untouched. Every future deck needing the management sense of *run* now pays nothing.

---

## 24. Roadmap

**Phase 0 — ship a deck without new tables (≈ 1 week).** Template file format,
`deck-validate`, `deck-plan`, `deck-build` with `deck_build_jobs`/`deck_build_items`, all
resolution going through the **existing** `AIService` chain and reusing only the existing
lookup cache. Materialise into `words`. Publish with the existing endpoint. This produces
a real 504 deck and, more usefully, a real measurement of cache hit rate on a word list.

**Phase 1 — the lexicon (≈ 1–2 weeks).** Three tables, `LexiconAIService`, write-through
from both the request path and the builder, `SenseValidator`, selection strategies 1/3/4,
`words.lexeme_sense_id`. Backfill the lexicon from existing `ai_lookup_entries` at the
current prompt version — a one-off script, free, and it seeds the store with everything
users have already paid for.

**Phase 2 — quality & control (≈ 1–2 weeks).** Enrichment, hint-based selection
(strategy 2), review states, admin monitoring + review + retry endpoints, the
vocably-admin screens.

**Phase 3 — scale & economics.** `refresh_lexicon` / `refresh_deck_words`, admin-triggered
builds, cost dashboards, optional batched AI ranking, translation fan-out to a second
native language.

Phases 0 and 1 are independently shippable and each leaves the system in a better state
than before. Phase 0 deliberately buys some content twice rather than blocking the first
deck on the lexicon migration — the amount is small and the feedback is worth more.

---

## 25. Pitfalls & edge cases

**Normalisation and identity**

* `normalize_lookup_input` case-folds, so `Polish`/`polish` and `March`/`march` collide
  into one lexeme. Accepted today for the cache; for the lexicon it means both readings
  live as senses of one lemma. Fine — but do not add stemming, ever: `run`/`running` are
  different cards.
* It also strips surrounding punctuation, which is right for `"run,"` and wrong for
  nothing that appears in a word list. Verify hyphenated entries (`well-being`) survive.
* Phrasal verbs and idioms exceed nothing in the cache but do hit
  `GroundedAIService._MAX_GROUNDABLE_CHARS` (40) for longer idioms — those silently skip
  the dictionary and take the generative path. Expected, but it changes the cost profile
  of an idioms deck; measure before assuming.
* A term appearing in two lessons of the same book (504 does this) must be a deliberate
  decision: same sense twice is a data bug, different senses is legitimate. The validator
  should flag it, not auto-resolve it.

**Concurrency and Celery**

* `acks_late` means every task may run twice. The conditional claim is what makes that
  safe — never replace it with a read-then-update.
* `beat` must stay at one replica. Nothing here is scheduled, but a future
  "resume stalled builds" entry would fire twice on two beats.
* `run_async` is mandatory in the task; `asyncio.run` breaks the *second* task in a
  worker via dead-loop connections, and it is very hard to trace.
* Never hold a transaction across a provider call. Under a 500-word build this is how the
  connection pool dies.

**Versioning**

* Pin `content_version` on the job. A deploy mid-build otherwise writes half a deck under
  a new prompt.
* A prompt bump must never enqueue regeneration. Guard it in review: any PR that touches
  `PROMPT_VERSION` and also touches a task module deserves a second look.

**Content and product**

* Zero valid senses after validation is a *generation failure*, not an empty word. Never
  materialise a card with an empty definition.
* Wrong-language output is the failure mode that survives schema validation and looks
  fine in a log. The script check on `native_meaning` catches it; keep it.
* `phonetic`'s NULL-vs-`""` distinction must survive into `lexemes`, or the backfill task
  re-asks permanent misses nightly forever.
* The system user owning official decks must be excluded from account-deletion paths and
  from admin user lists, or it will look like a real learner with 40,000 cards.
* Deck size vs the review queue: 504 cards is a lot to eyeball. Resist "review
  everything"; the confidence-sorted queue is the mechanism that makes review actually
  happen.
* Explore's `copy_deck_to` preserves `created_at` ordering — so the builder must write
  items in template order with increasing timestamps, or a saved copy of the book will be
  in the wrong sequence. This is the single easiest thing to get wrong and the hardest to
  notice.
