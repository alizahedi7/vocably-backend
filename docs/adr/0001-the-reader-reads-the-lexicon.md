# The reader reads the lexicon; it has no vocabulary table of its own

**Status:** accepted · 2026-09-29

## Context

The e-reader brief asked for a `vocabulary_cache(lemma, pos, senses JSONB)` table
as the "Level 2" store behind word lookups. It also asked for one LLM call per
unknown word, returning every sense of the word *and* the sense used in the
learner's sentence, with the whole package persisted.

The codebase already holds that knowledge. `lexemes`, `lexeme_senses` and
`lexeme_sense_translations` are keyed by lemma. They carry a stable id per
sense, review status, and a per-language split, and they survive a prompt bump.
`ai_lookup_entries` sits in front of them as a disposable request cache. The
flashcard lookup and the deck-build pipeline both go through one chain,
`lookup_chain()`, so that a word paid for once is free everywhere.

## Decision

The reader calls `lookup_chain()` with the **lemma of the tapped word, and
nothing else**. A Redis decorator sits in front of it for reader traffic only.
Choosing the sense a sentence uses is a separate step *after* the chain. It is
free when a single sense exists or when word overlap decides, and otherwise it
is an index-only model call memoised per (sense deck, sentence). That call never
writes the lexicon.

## Consequences

- One corpus. A word tapped in a book is free to the deck builder and to every
  flashcard lookup, and the reverse holds too. A second table would split the
  corpus in two and silently re-buy both halves.
- The shared senses are a fact about the word, never about one learner's
  paragraph. This is the same rule that keeps `interests` out of the lookup
  cache key.
- A cold *and* ambiguous word costs two calls instead of one. Only the first is
  per word, and the second is per sentence platform-wide, so the steady-state
  cost is paid per new sentence rather than per tap.
- The reader cannot store reader-only facts about a word, such as a CEFR level,
  without adding them to the lexicon, where every consumer sees them. That is
  intended: a fact worth having is worth having everywhere.
- A sense the lexicon lacks surfaces as `selection: none`, which is the input
  for the existing `SenseEnricher`, not a reason for a private store.

## Alternatives rejected

- **A separate `vocabulary_cache` table.** Duplicates the lexicon without its
  sense ids, review status or translation split, and forks the reuse
  guarantee.
- **One prompt returning every sense plus the contextual sense.** It is cheaper
  on the very first tap, but it puts one learner's sentence into the input of
  shared, durable content.
- **Storing the disambiguation in Postgres.** It is derived and cheap to
  recompute, so Redis with a TTL is the right weight, exactly as single-flight
  is.
