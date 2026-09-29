"""``Lemmatizer`` backed by simplemma — pure Python, MIT-licensed, dictionary
based, with no model to download and no C extension to build.

Chosen over spaCy (hundreds of MB of model for one function) and NLTK's
WordNet lemmatiser (a corpus download at runtime, and it wants a part of
speech the tap does not know). simplemma answers from a bundled word list in
microseconds and returns the input unchanged when it does not know it, which
is exactly the fallback the port promises.

A multi-word selection is passed through untouched: "gave up" → "give up" is
right, but "New York" → "new york" is a different word, and telling the two
apart is a job for the lookup prompt, not for a word list.
"""

from __future__ import annotations

from functools import lru_cache

import simplemma

from app.application.ports.lemmatizer import Lemmatizer

#: simplemma's codes are ISO 639-1 and it raises on a language it lacks.
_SUPPORTED = frozenset({"en", "de", "fr", "es", "it", "pt", "nl", "ru", "fa", "tr"})


class SimplemmaLemmatizer(Lemmatizer):
    def lemma(self, surface: str, *, language: str = "en") -> str:
        token = surface.strip()
        if not token or " " in token or language not in _SUPPORTED:
            return token
        return _lemma(token, language)


@lru_cache(maxsize=50_000)
def _lemma(token: str, language: str) -> str:
    try:
        result = simplemma.lemmatize(token, lang=language)
    except (ValueError, KeyError):
        return token
    # Case-folded because the lexicon is: "Ran" opening a sentence is "run".
    return str(result or token).casefold()
