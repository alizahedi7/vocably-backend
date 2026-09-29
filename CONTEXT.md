# Vocably

A vocabulary-learning app: learners collect words as cards, review them on a
Leitner schedule, and now read public-domain books with every word one tap from
its meaning. This glossary covers the words the vocabulary and reading features
share.

## Vocabulary knowledge

**Lexeme**:
A headword the platform knows, in its dictionary form, with every useful sense of it.
_Avoid_: vocabulary cache, dictionary entry, word (when the shared record is meant)

**Sense**:
One distinct meaning of a lexeme, with its definition, example, part of speech and label.
_Avoid_: meaning, definition (a definition is one field of a sense)

**Lookup**:
One request to know what a piece of text means, answered with a deck of senses.
_Avoid_: search, query, translation

**Lookup cache**:
The disposable record of lookups already answered; it can be thrown away without losing knowledge.
_Avoid_: dictionary, vocabulary cache

**Card**:
A learner's own copy of one sense of a word, in one deck, which they may edit.
_Avoid_: flashcard (in code), word (when the lexeme is meant)

**Lemma**:
The dictionary form a lexeme is filed under: *run* for *ran*, *running* and *runs*.
_Avoid_: stem, root, base word

**Surface form**:
A word exactly as it appears in text or as a learner typed it, before lemmatising.
_Avoid_: token, raw word

## Reading

**Book**:
A public-domain text in the library, made of chapters, that a learner can read once it is published.
_Avoid_: ebook, title, document

**Chapter**:
One entry in a book's table of contents, holding an ordered run of blocks.
_Avoid_: section, part (a part is a heading above several chapters)

**Block**:
The smallest addressable piece of a book's text: a paragraph, an in-chapter heading, a quotation or a run of verse.
_Avoid_: page, paragraph (when a heading or verse may be meant), chunk, token

**Canonical text**:
A block's text in the one normalised form that every position and tap is measured against; it never changes once the book is published.
_Avoid_: raw text, content

**Page**:
A screenful of blocks as one device lays them out; it exists only on the client and is never stored.
_Avoid_: using it for any stored unit

**Tap**:
A learner selecting a word or short phrase inside a block to look it up.
_Avoid_: click, highlight, selection (when stored)

**Contextual sense**:
The sense of a lexeme that one particular sentence uses, chosen from the senses the platform already holds.
_Avoid_: context definition, in-context meaning

**Passage translation**:
A whole block rendered into the learner's language, shared by everyone who reads that text.
_Avoid_: paragraph translation (in code), machine translation

**Reading position**:
Where one learner is in one book: a block and an offset into it.
_Avoid_: progress (when the position is meant), bookmark, last page

**Shelf**:
A learner's books with a reading position, most recently read first.
_Avoid_: library (the library is every published book), reading list

**Publication**:
The deliberate act that makes an ingested book visible in the library; ingesting never does it.
_Avoid_: release, activation
