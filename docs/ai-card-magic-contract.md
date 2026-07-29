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
  "suggestions": [
    {
      "context": "Movement",
      "part_of_speech": "verb",
      "native_meaning": "دویدن",
      "meaning": "to move fast on foot",
      "definition": "to move quickly using your legs, faster than walking",
      "examples": [
        "I run along the beach every morning before breakfast.",
        "She had to run to catch the last train to the airport."
      ],
      "synonyms": ["sprint", "jog", "dash"],
      "antonyms": [],
      "collocations": ["run fast", "run a marathon", "go for a run"],
      "example": "I run along the beach every morning before breakfast."
    }
  ]
}
```

Between 0 and 4 suggestions, ordered most common sense first. A monosemous word
returns exactly one — the deck is never padded to four.

## Field mapping

Each entry of `suggestions` is one card back, in deck order. `aiOptions[i]` maps
1:1 to `suggestions[i]`.

| Prototype binding | API field | Notes |
|---|---|---|
| `c.context` | `context` | The uppercase chip. Already capitalised; the design applies `text-transform`. |
| `c.pos` | `part_of_speech` | Italic label beside the chip. |
| `c.native` / `opt.native` | `native_meaning` | The 26px card headline. **Always in the user's own native language** — the prototype's `nativeLang === 'Persian' ? opt.native : opt.nativeEn` branch disappears; the server has already picked the language. |
| `c.gloss` / `opt.meaning` | `meaning` | Sub-headline under the native meaning. **In the detected language of the input**, not a fixed language — see below. |
| `c.definition` | `definition` | The "DEFINITION" body. Same detected-language rule as `meaning`. |
| `c.examples[].text` | `examples[]` | Map to `{ text }`: `examples.map(t => ({ text: t }))`. |
| `c.counter` | — | Client-computed: `` `${i + 1} / ${suggestions.length}` ``. |
| `c.defLabel`, `c.exLabel` | — | Client-side i18n strings; unchanged. |
| — | `synonyms`, `antonyms`, `collocations` | Not rendered by v7. Available for a future metadata row; may be empty arrays. |
| — | `example` | Compatibility mirror of `examples[0]`. New clients should read `examples`. |

`part_of_speech`, `native_meaning`, `definition` are strings and may be `""` if a
provider omits them; the arrays may be empty. Guard before rendering rather than
assuming presence.

## `status` — the edge cases

`status` reports how the server interpreted the raw text, and drives the notice
line above the deck. `notice` is a ready-to-display sentence; it is `null` exactly
when `status` is `"ok"`.

**The input is not assumed to be any particular language.** The server detects
whatever language the learner typed in — English, Persian, Spanish, Chinese,
anything — and every field except `native_meaning` (`meaning`, `definition`,
`examples`, `synonyms`, `antonyms`, `collocations`) is written in *that* detected
language, at the register of that language's standard dictionary (Longman/Oxford/
Merriam-Webster for English, and the equivalent authoritative reference for
others). `native_meaning` is always translated into the learner's own native
language, even when the input already was in that language.

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
      example: (opt.examples && opt.examples[0]) || '',
    },
  }));
  // …unchanged: close the sheet and flash "Back of card filled in"
};
```

`clearAiSuggestion` should restore `term` from `aiUndo` too, now that selecting can
change it.

The `aiCards` builder (line 2404) keeps its shape; only three lines change:

```js
context: opt.context,
pos: opt.part_of_speech,                          // was opt.pos
native: opt.native_meaning,                       // was the nativeLang ternary
gloss: opt.meaning,
definition: opt.definition,
examples: (opt.examples || []).map(text => ({ text })),
```

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

Lookups are **not** cached server-side today. Debounce on the client if you ever
wire the CTA to fire automatically instead of on tap.

## Keeping this in sync

`suggestions[]` is generated from `MeaningSuggestion` in
[ai_service.py](../app/application/ports/ai_service.py) and serialised by
[ai.py](../app/api/v1/schemas/ai.py). Renaming a field there is a breaking change
for this screen — update this table in the same commit.
