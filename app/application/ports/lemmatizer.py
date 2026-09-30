"""Port: surface form → dictionary form, offline and deterministic.

The lexicon is keyed by lemma. A reader taps *ran*, *running* or *runs* and must
land on the one lexeme for *run*, or the platform pays for — and stores — three
headwords for one word. The lookup path's ``normalize_lookup_input``
deliberately does no stemming (a learner typing "running" may want that card),
so lemmatisation is a reader-only step, applied before the chain.

Never a model call: this runs on every tap, and a wrong lemma from a model
would silently file a sense under the wrong headword for everyone.
"""

from __future__ import annotations

from typing import Protocol


class Lemmatizer(Protocol):
    def lemma(self, surface: str, *, language: str = "en") -> str:
        """The dictionary form of ``surface``, or ``surface`` itself when unknown."""
        ...
