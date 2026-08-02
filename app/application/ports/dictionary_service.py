"""Port: a source of ground-truth English dictionary entries.

Exists to take *meaning* out of the model's hands. Measured on our own lookup
prompts, a model asked to recall senses invents them — it produced a "Geometry"
sense for `decline` and a ball-possession sense for `bound`, both fluent and both
false. Handed the same words as data, it stops inventing, because the job changes
from recall to selection.

The port is deliberately thinner than any dictionary API's response: senses,
parts of speech, examples, and pronunciation. Everything else those APIs ship
(audio URLs, licences, source links) is bulk that would become billable prompt
tokens without changing an answer.

**Never load-bearing.** Every implementation may return ``None`` — for a miss, a
timeout, a rate limit, or an outage — and the caller must carry on to full AI
generation. A free dictionary is an optimisation, not a dependency: ours covers
67% of real learner input and answers 403/429/502 under load.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class DictionarySense:
    """One numbered sense as the dictionary states it, before any rewriting."""

    definition: str
    part_of_speech: str = ""
    #: The dictionary's own example, when it has one. Roughly half of senses do.
    example: str = ""


@dataclass(frozen=True, slots=True)
class DictionaryEntry:
    """Every sense a dictionary holds for one headword.

    Expect this to be *large and unranked*: the free Wiktionary mirror returns 63
    senses for "run" and 38 for "bound", archaic and dialectal ones mixed in
    among the common. Selecting the few a learner needs is the consumer's job.
    """

    term: str
    senses: list[DictionarySense] = field(default_factory=list)
    #: IPA, e.g. ``/ʌndəˈmaɪn/``. Worth carrying precisely because it is the one
    #: field a language model cannot be trusted to produce — it has no reliable
    #: memory of phonetic transcription, and a wrong IPA teaches a wrong sound.
    phonetic: str = ""

    def top(self, limit: int) -> list[DictionarySense]:
        """The first ``limit`` senses, preferring ones that carry an example.

        A cheap, deterministic relevance proxy: lexicographers illustrate the
        senses people actually use, so "has an example" correlates with "a
        learner will meet this". Ordering here rather than in the prompt keeps
        the expensive, non-deterministic step working on a short list.
        """
        ranked = sorted(self.senses, key=lambda s: not s.example)
        return ranked[:limit]


class DictionaryService(ABC):
    @abstractmethod
    async def look_up(self, term: str) -> DictionaryEntry | None:
        """Return the entry for ``term``, or ``None`` if there isn't one.

        Implementations must not raise for the ordinary failures — not found,
        rate limited, upstream 5xx, timeout. All of them mean the same thing to
        the caller: no grounding available, generate instead.
        """
