# AI Card Magic — client integration contract

How the **Add word** screen and the **AI Card Magic** overlay in
`Flashcard App v7 - AI Magic.dc.html` bind to `POST /api/v1/ai/lookup`.

The prototype ships a hard-coded `AI_SENSES` table and a `fakeSenses()` fallback.
This document replaces both with the real endpoint, field by field. Nothing in the
prototype's markup, layout, or animation needs to change — the response was shaped
to fit the card the design already draws.

## Endpoint

```
POST /api/v1/ai/lookup
Authorization: Bearer <access token>
Content-Type: application/json

{ "term": "run" }
```

`term` is whatever the learner typed in the `addForm.term` input: a word, a phrase,
an idiom, or a full sentence, in **any language** — English, Persian, Spanish,
Chinese, or anything else. Max 200 characters — a longer body is rejected with
`422` before any model call. Do **not** pre-clean, translate, or lowercase it on
the client; the server needs the raw text to detect its language and any typo.

Native language, age range, and interests are read server-side from the
authenticated user's profile. Do not send them.

### Response

```json
{
  "term": "run",
  "status": "ok",
  "notice": null,
  "phonetic": "/ɹʌn/",
  "lookup_id": "9f2c…",
  "suggestions": [
    {
      "native_meaning": "دویدن",
      "definition": "to move quickly using your legs, faster than walking",
      "example": "I run along the beach every morning before breakfast.",
      "context": "Movement",
      "part_of_speech": "verb"
    }
  ]
}
```

Between 0 and 4 suggestions, ordered most common sense first. A monosemous word
returns exactly one — the deck is never padded to four.

### `lookup_id` — the handle for rating a card back

A stable id for *this deck of senses*: the term as resolved, at the prompt
version that wrote them, for this learner's native language and age bucket. Send
it back on `POST /ai/feedback` to record a thumb on one of the cards.

**Empty whenever there is nothing to rate**, and that is the ordinary case for an
`unsupported` answer — treat it exactly as you treat an empty `phonetic`: render
no control at all, never an error, and never a control that posts nothing.

It is deterministic rather than random, which is the point: two learners who look
the same word up rate one deck between them instead of two. It names a shared
dictionary entry (`ai_lookup_entries.entry_hash`) and carries nothing about the
user, so it is safe to hold on the client for as long as the suggestions are on
screen. It changes when the prompt version does, so a verdict on the old cards is
never counted against the new.

### `phonetic` — optional, and frequently absent

IPA for `term`, one value for the whole lookup rather than per sense (the senses
of a headword share a pronunciation). **Treat it as optional and hide the element
when it is `""`.** It is populated only when the word was found in the
dictionary, which is about two thirds of real learner input — typos, most idioms
and non-English terms come back without one.

It is deliberately left empty rather than filled in by the language model.
Phonetic transcription is exactly the kind of detail a model reproduces
confidently and wrongly, and a wrong IPA teaches a learner to mispronounce the
word — worse than showing nothing. Adding it is backwards-compatible: clients
that ignore the field are unaffected.

**Pass it back when saving the card.** `POST /words` accepts `phonetic`, and the
value the lookup just returned is the cheapest one there is — the dictionary has
already been consulted. A card saved without it is not wrong, only slower to
show one: `vocably.ai.backfill_phonetics` fills in cards that have none. Sending
`""` and omitting the field mean the same thing, so a client that has no
transcription should simply leave it out.

The client must **drop the phonetic if the learner edits the term after the
lookup**. `/rʌn/` under a card that now reads "ran" is a wrong transcription, and
the server applies the same rule to `PATCH /words/{id}`: a changed `term` with no
`phonetic` in the same request clears the stored one.

> **Breaking change — clients built against the previous shape must update.**
> A suggestion now carries **exactly these five fields**. Removed: `meaning`
> (the gloss sub-headline), `examples[]` (now a single `example`), `synonyms`,
> `antonyms`, `collocations`. Any code reading `opt.meaning` or `opt.examples[0]`
> breaks. See [Migrating from the previous shape](#migrating-from-the-previous-shape).

## Field mapping

Each entry of `suggestions` is one card back, in deck order. `aiOptions[i]` maps
1:1 to `suggestions[i]`.

| Prototype binding | API field | Notes |
|---|---|---|
| `c.native` / `opt.native` | `native_meaning` | The 26px card headline. **Always in the user's own native language** — the prototype's `nativeLang === 'Persian' ? opt.native : opt.nativeEn` branch disappears; the server has already picked the language. |
| `c.definition` | `definition` | The "DEFINITION" body. **Always English**, in learner-dictionary style — see below. |
| `c.examples[].text` | `example` | Now a single string. Wrap if the design still wants a list: `[{ text: opt.example }]`. |
| `c.context` | `context` | The uppercase chip. Already capitalised; the design applies `text-transform`. **Always English.** |
| `c.pos` | `part_of_speech` | Italic label beside the chip. **Always English** (`"noun"`, `"verb"`, `"adjective"`, `"adverb"`, `"phrasal verb"`, `"idiom"`, …), so you can map it to your own localised label. |
| `c.gloss` | — | **Gone.** There is no sub-headline field any more; drop the row from the card or leave it blank. |
| `c.counter` | — | Client-computed: `` `${i + 1} / ${suggestions.length}` ``. |
| `c.defLabel`, `c.exLabel` | — | Client-side i18n strings; unchanged. |

All five are plain strings and every one may be `""` if a provider omits it.
Guard before rendering rather than assuming presence.

## Which language is each field in?

This is the part most likely to surprise you, so it is worth stating plainly. The
server detects whatever language the learner typed — English, Persian, Spanish,
Chinese, anything — and each field then has a **fixed** language:

| Field | Language | Why |
|---|---|---|
| `native_meaning` | The learner's **native language**, in its own script | The headline: what this word means to *them*. Set from their profile, even when the term was already in that language. |
| `definition` | **Always English** | English is the app's single reference language, so every card reads in one consistent dictionary voice regardless of what was looked up. A Persian or Chinese term still gets an English definition. |
| `context` | **Always English** | A short label, so the client can localise it itself. |
| `part_of_speech` | **Always English** | A known small value set — map it to your own strings. |
| `example` | The **term's own language** | An example sentence has to show the word in use, so it is written in whatever language the term is. |
| `term` (top level) | The **term's own language** | |

So a Persian speaker looking up `ephemeral` gets a Persian headline, an English
definition, and an English example. The same user looking up `دویدن` gets a
Persian headline, an English definition, and a **Persian** example.

Nothing about the learner's profile is sent by the client — native language, age
range, and interests are all read server-side from the authenticated user.

## What `definition` looks like

Written in the general style of a learner's dictionary (Longman,
Merriam-Webster Learner's) — but readability for the learner wins over strict
dictionary convention, so treat the shape below as typical rather than
guaranteed:

- **Starts lowercase and has no trailing full stop.** This one is reliable — do
  not add a period in the UI, and do not capitalise it with CSS. `"to move using
  your legs, going faster than when you walk"`.
- **Usually one sentence, roughly 8–25 words.** Budget for two lines at the
  card's width, three at the largest accessibility text size. Don't hard-clamp to
  one line.
- **Normally fits the headword's grammar** — a verb reads `"to …"`, a noun
  `"a person who …"`, an adjective as a description. Useful if you ever render
  the part of speech and the definition on one line, but not something to rely on.
- **May open with a short parenthesised note**: `"(of a machine) to operate
  correctly"`, `"(informal)"`. If you want to grey those out, match a leading
  `(...)` group — and handle its absence, since these appear only when they help.
- **Non-English terms get the same treatment, in English** — a real definition,
  not a translation. `تعارف` returns something like `"a Persian social custom of
  repeatedly offering or refusing something out of politeness"`, not
  `"politeness"`. These run to the longer end, so leave room.

## `status` — the edge cases

`status` reports how the server interpreted the raw text, and drives the notice
line above the deck. `notice` is a ready-to-display sentence; it is `null` exactly
when `status` is `"ok"`.

**The input is not assumed to be any particular language.** The server detects
whatever language the learner typed in and reports how it interpreted it. See
[Which language is each field in?](#which-language-is-each-field-in) for what
that means per field.

| `status` | What happened | What the client shows |
|---|---|---|
| `ok` | Clean input in whatever language it was written, including the learner's own native language. | The deck. No notice. |
| `corrected` | Typo resolved, in the language it was written. `term` is the corrected spelling. | Notice + deck. |
| `extracted` | A sentence was submitted; the key word or idiom was pulled out, in the sentence's own language. | Notice + deck. |
| `translated` | **Legacy — no longer emitted.** Previously fired when the input was in the learner's native language and got pivoted to English; native-language input is now a normal `ok` lookup defined in that language. Kept in the schema for older clients only. | Notice + deck, if an old server build ever sends it. |
| `unsupported` | Not a recognisable lexical item. `suggestions` is `[]`. | The existing `aiNoResults` empty state, using `notice` as the message. |

Two consequences for the prototype's logic:

1. **`term` may differ from what the learner typed.** It is the term the cards
   actually describe. On `selectAiOption`, write `term` into `addForm.term` as well
   as filling the back — otherwise a corrected typo saves the card under the
   misspelling. The v7 code does not do this today.
2. **Render `notice` whenever it is non-null.** Put it in the overlay header under
   `aiOverlayWord`, or reuse the `L.noSuggestions` slot. A `translated` or
   `corrected` result with no notice shown looks like the app ignored the input.

`unsupported` is a normal `200`, not an error. It is the model declining to invent
a definition, which is deliberate — treat it as the empty state, not a failure.

## Wiring the prototype's handlers

`askAI` (v7 line 1793) currently sets a `setTimeout` and reads `AI_SENSES`.
Replace the timer body with the request; keep the staged `aiStep` animation, which
still reads well over real latency (~5–20s on a busy gateway):

```js
askAI = async () => {
  const term = this.state.addForm.term.trim();
  if (!term || this.state.aiState === 'loading') return;
  this.setState({ aiSheetOpen: true, aiState: 'loading', aiTerm: term, aiStep: 0 });
  [1, 2, 3].forEach(n => {
    this['_aiStep' + n] = setTimeout(() => this.setState({ aiStep: n }), n * 520);
  });

  try {
    const res = await fetch('/api/v1/ai/lookup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({ term }),
    });
    if (!res.ok) throw new Error(String(res.status));
    const data = await res.json();
    if (this.state.aiTerm !== term) return;   // the learner kept typing; drop this reply
    this.setState({
      aiState: data.suggestions.length ? 'results' : 'empty',
      aiOptions: data.suggestions,
      aiResolvedTerm: data.term,
      aiNotice: data.notice,
      aiIndex: 0,
    });
  } catch {
    this.setState({ aiState: 'empty', aiOptions: [], aiNotice: null });
  }
};
```

The stale-response guard matters: `onTermChange` already resets `aiState` on every
keystroke, so without it a slow reply for an older term can overwrite a newer one.

`selectAiOption` (line 1812) loses its language branch and gains the term write-back:

```js
selectAiOption = (i) => {
  const opt = this.state.aiOptions[i];
  if (!opt) return;
  if (this.state.aiIndex !== i) { this.goAiCard(i); return; }
  this.setState(s => ({
    aiSelected: i,
    aiUndo: s.aiUndo || {
      term: s.addForm.term,
      meaning: s.addForm.meaning,
      definition: s.addForm.definition,
      example: s.addForm.example,
    },
    addForm: {
      ...s.addForm,
      term: s.aiResolvedTerm || s.addForm.term,   // NEW — commit the resolved term
      meaning: opt.native_meaning,                 // was: nativeLang === 'Persian' ? …
      definition: opt.definition,
      example: opt.example || '',                  // was: (opt.examples || [])[0]
    },
  }));
  // …unchanged: close the sheet and flash "Back of card filled in"
};
```

`clearAiSuggestion` should restore `term` from `aiUndo` too, now that selecting can
change it.

The `aiCards` builder (line 2404) keeps its shape:

```js
context: opt.context,
pos: opt.part_of_speech,                          // was opt.pos
native: opt.native_meaning,                       // was the nativeLang ternary
definition: opt.definition,
examples: opt.example ? [{ text: opt.example }] : [],   // was opt.examples.map(…)
// `gloss` has no source any more — remove the binding and the sub-headline row.
```

## Migrating from the previous shape

| Was | Now |
|---|---|
| `opt.meaning` | **Removed.** No gloss field exists; delete the sub-headline. |
| `opt.examples` (array) | `opt.example` (single string) |
| `opt.examples[0]` | `opt.example` |
| `opt.synonyms` / `antonyms` / `collocations` | **Removed.** Not rendered by v7, so nothing to change unless you had built ahead. |
| `definition` in the detected language | `definition` **always in English** |
| `context` / `part_of_speech` in the detected language | **always in English** |

`native_meaning`, `context`, and `part_of_speech` keep their names and meaning.
A client that only read `native_meaning`, `definition`, `examples[0]`, `context`
and `part_of_speech` needs exactly one change: `examples[0]` → `example`.

## Saving the card

The back the learner keeps is persisted by `POST /api/v1/words`, which carries
`definition` alongside `meaning` and `example`:

```json
{
  "deck_id": "…",
  "term": "run",
  "meaning": "دویدن",
  "definition": "to move quickly using your legs, faster than walking",
  "example": "I run along the beach every morning before breakfast.",
  "sense_label": "sense · movement"
}
```

`definition` is optional on create and on `PATCH /api/v1/words/{id}`, max 2000
characters, and returned on every `WordOut`. Three rules:

- A card written by hand has `definition: null`, not `""` — the server folds
  blank input to `NULL` so "absent" has one representation.
- **Omitting** it in a `PATCH` leaves the stored value alone; sending `""` is
  how the learner clears it. That distinction is what keeps a client older than
  the field from silently wiping a definition it never knew about — which
  matters, because Android installs lag the web build by weeks.
- Nothing else derives from it. `sense_label` still carries which sense was
  chosen (`sense · <context>`), and the study screen does not render the
  definition today.

## Failure modes

| Status | Cause | Client behaviour |
|---|---|---|
| `401` | Missing/expired token. | Refresh and retry once, then send the user to sign-in. |
| `422` | Empty term, or over 200 characters. | Validate before sending; the CTA is already hidden while `term` is empty. |
| `502` | Provider unreachable, or returned an unusable response after one retry. | Show the empty state and keep the manual fields editable — the learner can always write the back by hand. That path is the design's `aiWriteOwnLabel`, so nothing new is needed. |

## Caching

Lookups **are** cached server-side, shared across all users and keyed on the term
(case- and punctuation-insensitive), the learner's native language, and their age
band. Two consequences for the client:

- A repeated term returns in milliseconds instead of seconds. The staged `aiStep`
  animation should not assume a multi-second wait — let it be cut short.
- The response for a given term is stable, so there is nothing to gain from
  caching it again on the device.

Debounce on the client if you ever wire the CTA to fire automatically instead of
on tap; a cache hit is cheap for the server but still a round trip.

## Keeping this in sync

`suggestions[]` is generated from `MeaningSuggestion` in
[ai_service.py](../app/application/ports/ai_service.py) and serialised by
[ai.py](../app/api/v1/schemas/ai.py). Renaming a field there is a breaking change
for this screen — update this table in the same commit.
