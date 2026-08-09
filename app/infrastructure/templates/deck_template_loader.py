"""Reading deck templates off disk, and refusing the ones that are wrong.

Templates live in ``content/decks/<slug>/`` as two YAML files rather than as
database rows, and that is a deliberate trade:

*Against files:* an admin cannot author a deck in the browser.

*For files — decisive:* a new deck's word list arrives as a **pull request**.
Ordering, lesson boundaries and sense pins get read by a human before a single
token is spent, the file is diffable against the source, and there is no template
editor to build before the first deck can ship. The database still materialises
the plan — as ``deck_build_items``, written once at plan time — so there is
exactly one place structure can drift from, and it is a git file.

Everything here runs offline and spends nothing. ``make deck-validate`` is meant
to be cheap enough to run on every edit.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml

from app.domain.entities.deck_build import SenseHint
from app.domain.entities.deck_template import (
    SELECTABLE_STRATEGIES,
    DeckTemplate,
    GenerationConfig,
    TemplateError,
    TemplateReport,
    TemplateSource,
    TemplateUnit,
    TemplateWord,
)
from app.domain.enums import SenseSelection

#: Where templates live, relative to the repository root.
TEMPLATE_ROOT = Path("content/decks")

DECK_FILE = "deck.yaml"
WORDS_FILE = "words.yaml"

#: A card front is a word, a phrase or an idiom — never a sentence. Well above
#: the longest real idiom and far below anything that could be prose pasted from
#: a source.
MAX_TERM_CHARS = 120


def load_template(slug: str, *, root: Path | None = None) -> DeckTemplate:
    """Read and parse one template. Raises :class:`TemplateError` on anything wrong."""
    base = (root or TEMPLATE_ROOT) / slug
    deck_path = base / DECK_FILE
    words_path = base / WORDS_FILE
    if not deck_path.is_file() or not words_path.is_file():
        raise TemplateError(f"template {slug!r} needs both {DECK_FILE} and {WORDS_FILE} in {base}")

    deck_raw = deck_path.read_bytes()
    words_raw = words_path.read_bytes()
    deck_doc = _as_mapping(yaml.safe_load(deck_raw) or {}, DECK_FILE)
    words_doc = _as_mapping(yaml.safe_load(words_raw) or {}, WORDS_FILE)

    declared_slug = str(deck_doc.get("slug") or "").strip()
    if declared_slug and declared_slug != slug:
        # A slug that disagrees with its directory means a copied template whose
        # header was not updated — and it would later build under the wrong name.
        raise TemplateError(f"{DECK_FILE} declares slug {declared_slug!r} but lives in {slug!r}/")

    units = _parse_units(words_doc)
    template = DeckTemplate(
        slug=slug,
        name=_required(deck_doc, "name"),
        version=str(deck_doc.get("version") or "1.0.0"),
        name_fa=str(deck_doc.get("name_fa") or ""),
        category=str(deck_doc.get("category") or "general"),
        description=str(deck_doc.get("description") or ""),
        description_fa=str(deck_doc.get("description_fa") or ""),
        hue=int(deck_doc.get("hue") or 262),
        icon=str(deck_doc.get("icon") or ""),
        official=bool(deck_doc.get("official", True)),
        source=_parse_source(deck_doc.get("source")),
        generation=_parse_generation(deck_doc.get("generation")),
        units=units,
        # Both files, in a fixed order: the hash has to change when either does.
        content_hash=hashlib.sha256(deck_raw + b"\x1f" + words_raw).hexdigest(),
    )
    _check_structure(template, deck_doc.get("structure"))
    return template


def validate_template(template: DeckTemplate) -> TemplateReport:
    """Everything worth telling a human before they spend money on a build."""
    warnings: list[str] = []
    seen: dict[str, list[str]] = {}

    for _, unit, word in template.words_in_order():
        seen.setdefault(word.term.strip().casefold(), []).append(unit.name)

    duplicates = tuple((term, tuple(places)) for term, places in seen.items() if len(places) > 1)
    if duplicates:
        warnings.append(
            f"{len(duplicates)} term(s) appear more than once — the same sense twice is a "
            "data bug, two different senses is legitimate, and only you can tell which"
        )

    if not template.source.rights and template.source.title:
        warnings.append(
            "source.title is set but source.rights is empty — state what was taken from "
            "the source and what was not"
        )
    if template.generation.enrichment and not any(
        word.hint.gloss or word.hint.is_pinned for _, _, word in template.words_in_order()
    ):
        # Enrichment can only fire when something says which sense is wanted, so
        # leaving it on with no hints anywhere is a setting that will never apply.
        warnings.append(
            "enrichment is allowed but no word carries a hint or a pin — it can never fire"
        )
    return TemplateReport(template=template, duplicates=duplicates, warnings=tuple(warnings))


def list_templates(root: Path | None = None) -> list[str]:
    base = root or TEMPLATE_ROOT
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if (p / DECK_FILE).is_file())


# ── Parsing ──────────────────────────────────────────────────


def _parse_units(doc: Mapping[str, Any]) -> tuple[TemplateUnit, ...]:
    raw_units = doc.get("units")
    if not isinstance(raw_units, list) or not raw_units:
        raise TemplateError(f"{WORDS_FILE} must contain a non-empty `units:` list")

    units: list[TemplateUnit] = []
    for index, raw in enumerate(raw_units):
        unit = _as_mapping(raw, f"{WORDS_FILE} unit {index}")
        name = str(unit.get("name") or "").strip()
        if not name:
            raise TemplateError(f"unit {index} has no name")
        if len(name) > 40:
            # Matches deck_units.name, which the client renders as a chip.
            raise TemplateError(f"unit name {name!r} exceeds 40 characters")
        words = tuple(_parse_word(entry, name) for entry in _as_list(unit.get("words"), name))
        if not words:
            raise TemplateError(f"unit {name!r} has no words")
        units.append(TemplateUnit(name=name, words=words))
    return tuple(units)


def _parse_word(entry: Any, unit_name: str) -> TemplateWord:
    if isinstance(entry, str):
        return TemplateWord(term=_check_term(entry, unit_name))
    if not isinstance(entry, Mapping):
        raise TemplateError(f"unit {unit_name!r} has a word that is neither text nor a mapping")

    term = _check_term(str(entry.get("term") or ""), unit_name)
    sense = entry.get("sense")
    part_of_speech = context = ""
    if sense is not None:
        sense_map = _as_mapping(sense, f"sense of {term!r}")
        part_of_speech = str(sense_map.get("pos") or sense_map.get("part_of_speech") or "").strip()
        context = str(sense_map.get("context") or "").strip()
        if bool(part_of_speech) != bool(context):
            # Half a pin is not a pin: the sense key is derived from both, so one
            # alone would silently degrade to "no pin" at build time.
            raise TemplateError(
                f"{term!r} pins only half a sense — `sense:` needs both `pos` and `context`"
            )

    gloss = str(entry.get("hint") or "").strip()
    if len(gloss) > 200:
        # A hint disambiguates; anything this long is a definition, and a
        # definition is the one thing that must not be copied from a source.
        raise TemplateError(f"{term!r} has a hint longer than 200 characters")

    return TemplateWord(
        term=term,
        hint=SenseHint(part_of_speech=part_of_speech, context=context, gloss=gloss),
    )


def _check_term(term: str, unit_name: str) -> str:
    cleaned = term.strip()
    if not cleaned:
        raise TemplateError(f"unit {unit_name!r} contains an empty term")
    if len(cleaned) > MAX_TERM_CHARS:
        raise TemplateError(f"term {cleaned[:40]!r}… is too long to be a card front")
    return cleaned


def _parse_source(raw: Any) -> TemplateSource:
    if raw is None:
        return TemplateSource()
    doc = _as_mapping(raw, "source")
    authors = doc.get("authors")
    return TemplateSource(
        title=str(doc.get("title") or ""),
        authors=tuple(str(a) for a in _as_list(authors, "source.authors")) if authors else (),
        edition=str(doc.get("edition") or ""),
        isbn=str(doc.get("isbn") or ""),
        url=str(doc.get("url") or ""),
        rights=str(doc.get("rights") or "").strip(),
    )


def _parse_generation(raw: Any) -> GenerationConfig:
    if raw is None:
        return GenerationConfig()
    doc = _as_mapping(raw, "generation")
    strategies = doc.get("sense_selection")
    parsed: tuple[SenseSelection, ...]
    if strategies is None:
        parsed = GenerationConfig().strategies
    else:
        parsed = tuple(_parse_strategy(s) for s in _as_list(strategies, "sense_selection"))
        if not parsed:
            raise TemplateError("`sense_selection` may not be an empty list")

    enrichment = doc.get("enrichment", "allowed")
    return GenerationConfig(
        native_language=str(doc.get("native_language") or "Persian"),
        register=str(doc.get("register") or "adult"),
        strategies=parsed,
        enrichment=str(enrichment).strip().casefold() in {"allowed", "true", "yes", "on"},
        max_senses=int(doc.get("max_senses") or 4),
    )


def _parse_strategy(value: Any) -> SenseSelection:
    try:
        strategy = SenseSelection(str(value).strip().casefold())
    except ValueError as exc:
        raise TemplateError(f"unknown sense-selection strategy {value!r}") from exc
    if strategy not in SELECTABLE_STRATEGIES:
        raise TemplateError(f"strategy {strategy.value!r} cannot be chosen by a template")
    return strategy


def _check_structure(template: DeckTemplate, raw: Any) -> None:
    """Assert the shape the author says the source has.

    Asserted rather than derived on purpose: a word list that silently lost an
    entry is otherwise invisible until a learner notices Lesson 12 has eleven
    words, by which time the deck is published.
    """
    if raw is None:
        return
    doc = _as_mapping(raw, "structure")

    expected_units = doc.get("expected_units")
    if expected_units is not None and int(expected_units) != template.unit_count:
        raise TemplateError(
            f"structure says {expected_units} units, {WORDS_FILE} has {template.unit_count}"
        )

    expected_words = doc.get("expected_words")
    if expected_words is not None and int(expected_words) != template.word_count:
        raise TemplateError(
            f"structure says {expected_words} words, {WORDS_FILE} has {template.word_count}"
        )

    per_unit = doc.get("words_per_unit")
    if per_unit is not None:
        wrong = [u.name for u in template.units if len(u.words) != int(per_unit)]
        if wrong:
            raise TemplateError(
                f"structure says {per_unit} words per unit; these differ: {', '.join(wrong[:5])}"
                + ("…" if len(wrong) > 5 else "")
            )


# ── YAML helpers ─────────────────────────────────────────────


def _as_mapping(value: Any, what: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TemplateError(f"{what} must be a mapping, got {type(value).__name__}")
    return value


def _as_list(value: Any, what: str) -> Iterable[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TemplateError(f"{what} must be a list, got {type(value).__name__}")
    return value


def _required(doc: Mapping[str, Any], key: str) -> str:
    value = str(doc.get(key) or "").strip()
    if not value:
        raise TemplateError(f"{DECK_FILE} is missing required key `{key}`")
    return value
