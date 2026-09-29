# Book text is blocks addressed by character offsets, not pages or tokens

**Status:** accepted · 2026-09-29

## Context

The brief asked for Book → Chapter → Page/Paragraph → Tokens, with token or word
offsets for deterministic highlighting. Every reading position, every tap and
every cached paragraph translation needs a stable address into the text, and
changing that address later means rewriting every stored position.

## Decision

A chapter is an ordered list of **blocks**: paragraphs, headings, quotations
and verse. A block's text is *canonical*: NFC, zero-width characters removed,
whitespace collapsed. It is **immutable once the book is published**, which
`books.content_hash` enforces by refusing a changed re-ingest of a public book.
A location is `(block_id, char_offset)`, and a tap is
`(block_id, char_start, char_end)`. The server stores no pages and no tokens.

## Consequences

- Pages are the client's job. They depend on font size, screen and
  accessibility settings, none of which the server can know.
- No token table. A novel would be ~130,000 rows for no query that needs
  them. The client tokenises on tap, and the server validates offsets against
  the block and falls back gracefully when they do not fit.
- Because the text cannot change, the server can derive the tapped sentence
  itself. That makes sense disambiguation shareable and untrusting of the
  client.
- `text_hash` per block lets a paragraph translation be keyed by content, so
  the same paragraph in two editions is translated once. It also makes "is
  this free text a book's?" a single index lookup.
- A corrected edition of a published book cannot be swapped in place. It is
  unpublished and re-ingested, or ingested alongside. That constraint is the
  price of positions that never move.

## Alternatives rejected

- **Server-side pages.** They are wrong on every device but one.
- **Token rows with offsets.** They multiply storage by the word count and
  still need the canonical text to be immutable.
- **One text column per chapter with global offsets.** Every read then loads
  the whole chapter, and a paragraph has no identity to cache a translation
  against.
