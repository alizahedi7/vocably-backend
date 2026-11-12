"""The logos official decks draw, read off disk and served whole.

``content/deck-logos/logos.yaml`` maps the ``icon`` slug a deck template names
to what a client draws for it: a short **label** ("504", "IELTS") or a
one-colour **glyph** kept as an ``.svg`` beside the manifest.

It lives in git for the reason deck templates do: a logo arrives in the same
pull request as the deck it belongs to and is looked at by a human. It is served
as *one* document rather than one image per deck because a badge has to be right
on the first frame — a client holds the whole catalogue from its last launch, so
nothing is fetched while a list is being drawn.

Every glyph is checked here, once, at load: it is markup a client will render,
so it may carry no script, no handler and no outside reference.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

#: Where the catalogue lives, relative to the repository root.
LOGO_ROOT = Path("content/deck-logos")
MANIFEST_FILE = "logos.yaml"

#: A label is a title set inside a 44px square: "IELTS" fits, a word does not.
MAX_LABEL_CHARS = 6
#: A glyph is a pictogram, not an illustration. The whole catalogue is sent in
#: one response and kept on the device, so each entry stays small.
MAX_GLYPH_BYTES = 4096

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
#: Anything that could run, load or point somewhere else. Deliberately blunt:
#: a pictogram needs none of these, so there is no legitimate use to weigh.
_FORBIDDEN_RE = re.compile(
    r"<\s*(script|foreignObject|style|image|use|a|iframe)\b|\bon\w+\s*=|href\s*=|url\s*\(|<!|javascript:",
    re.IGNORECASE,
)
_VIEWBOX_RE = re.compile(r'viewBox="0 0 (\d+(?:\.\d+)?) (\d+(?:\.\d+)?)"')


class DeckLogoError(ValueError):
    """A catalogue that cannot be served. Raised at load, never to a learner."""


@dataclass(frozen=True, slots=True)
class DeckLogo:
    """What a client draws for one slug: exactly one of the two is set."""

    label: str = ""
    svg: str = ""


@dataclass(frozen=True, slots=True)
class DeckLogoCatalog:
    logos: dict[str, DeckLogo]
    #: Changes exactly when the content does. Clients send it back as
    #: ``If-None-Match`` and are answered 304 when they already hold this.
    version: str

    def has(self, slug: str) -> bool:
        return slug in self.logos


def load_catalog(root: Path | None = None) -> DeckLogoCatalog:
    """Read and check the catalogue. Raises :class:`DeckLogoError` on anything wrong."""
    base = root or LOGO_ROOT
    manifest = base / MANIFEST_FILE
    if not manifest.is_file():
        return _catalog({})

    doc = yaml.safe_load(manifest.read_bytes()) or {}
    if not isinstance(doc, dict):
        raise DeckLogoError(f"{MANIFEST_FILE} must be a mapping of slug to logo")

    logos: dict[str, DeckLogo] = {}
    for raw_slug, entry in doc.items():
        slug = str(raw_slug)
        if not _SLUG_RE.match(slug):
            raise DeckLogoError(f"logo slug {slug!r} must be lowercase letters, digits and dashes")
        logos[slug] = _parse_entry(slug, entry, base)
    return _catalog(logos)


@lru_cache(maxsize=1)
def deck_logo_catalog() -> DeckLogoCatalog:
    """The catalogue this deploy ships. Read once: it only changes with a deploy."""
    return load_catalog()


def _catalog(logos: dict[str, DeckLogo]) -> DeckLogoCatalog:
    canonical = json.dumps(
        {slug: [logo.label, logo.svg] for slug, logo in sorted(logos.items())},
        separators=(",", ":"),
    )
    return DeckLogoCatalog(logos=logos, version=hashlib.sha256(canonical.encode()).hexdigest()[:16])


def _parse_entry(slug: str, entry: Any, base: Path) -> DeckLogo:
    if not isinstance(entry, dict) or set(entry) not in ({"label"}, {"glyph"}):
        raise DeckLogoError(f"logo {slug!r} needs exactly one of 'label' or 'glyph'")

    if "label" in entry:
        label = str(entry["label"]).strip()
        if not 1 <= len(label) <= MAX_LABEL_CHARS:
            raise DeckLogoError(f"logo {slug!r} label must be 1-{MAX_LABEL_CHARS} characters")
        return DeckLogo(label=label)

    name = str(entry["glyph"])
    path = base / name
    # A bare file name beside the manifest, never a path that climbs out of it.
    if Path(name).name != name or path.suffix != ".svg" or not path.is_file():
        raise DeckLogoError(f"logo {slug!r} names glyph {name!r}, which is not an .svg in {base}")
    return DeckLogo(svg=_checked_svg(slug, path))


def _checked_svg(slug: str, path: Path) -> str:
    raw = path.read_bytes()
    if len(raw) > MAX_GLYPH_BYTES:
        raise DeckLogoError(f"logo {slug!r} glyph is over {MAX_GLYPH_BYTES} bytes")
    svg = raw.decode("utf-8").strip()
    if not svg.startswith("<svg") or not svg.endswith("</svg>"):
        raise DeckLogoError(f"logo {slug!r} glyph is not a single <svg> element")
    if _FORBIDDEN_RE.search(svg):
        raise DeckLogoError(f"logo {slug!r} glyph carries a script, a style or a reference")
    box = _VIEWBOX_RE.search(svg)
    if box is None or box.group(1) != box.group(2):
        raise DeckLogoError(f'logo {slug!r} glyph needs a square viewBox="0 0 N N"')
    return svg
