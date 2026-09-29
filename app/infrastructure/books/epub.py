"""Turn an EPUB into chapters of clean blocks. Pure: bytes in, dataclasses out.

Written against the standard library's ``zipfile`` plus BeautifulSoup, and
**not** against ``ebooklib``. That library is AGPL-3.0, and pulling it into a
network service is a licence decision this module should not make on the
project's behalf. An EPUB is a zip with one XML manifest and a spine of XHTML
files; reading that takes a page of code, and the page is here.

Two sources, two kinds of dirt:

*Standard Ebooks*
    Semantically marked EPUB3. Every ``<section>`` carries ``epub:type`` —
    ``chapter``, ``part``, ``titlepage``, ``imprint``, ``colophon``,
    ``uncopyright`` — so paratext is identified by name and each chapter is its
    own file. This is the clean path and the reason it is the preferred source.

*Project Gutenberg*
    Fifty years of volunteer HTML. The licence lives in the text itself
    between ``*** START OF THE PROJECT GUTENBERG EBOOK`` and ``*** END OF``
    markers, a whole novel may be a handful of files split at arbitrary
    points, chapters are whatever heading level the transcriber used, and
    illustrated editions put the drop-cap letter in an image's ``alt`` and an
    illustration's caption inside the chapter heading. So the parser flattens
    the whole spine into one stream of blocks, cuts it at the markers, and
    segments chapters by heading level when the markup does not say where
    they are.

What comes out is *canonical text*: NFC-normalised, whitespace-collapsed,
soft hyphens and footnote markers removed. Every character offset a client
ever sends is measured against it, which is why the normalisation lives in
one function (:func:`canonical_text`) and is deliberately boring.
"""

from __future__ import annotations

import hashlib
import posixpath
import re
import unicodedata
import warnings
import zipfile
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from io import BytesIO
from typing import Final
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup, Tag, XMLParsedAsHTMLWarning

from app.domain.enums import BlockKind

_OPF_NS: Final = {
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}
_CONTAINER_NS: Final = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}

#: ``epub:type`` tokens whose sections are paratext, never reading matter.
#: Deliberately a list of what to *drop*, so an unknown token (a new Standard
#: Ebooks convention, a transcriber's invention) is kept and a human sees it
#: in the chapter list, rather than silently losing a preface.
_PARATEXT_TYPES: Final = frozenset(
    {
        "titlepage",
        "halftitlepage",
        "imprint",
        "copyright-page",
        "colophon",
        "uncopyright",
        "toc",
        "loi",
        "lot",
        "landmarks",
        "endnotes",
        "footnotes",
        "glossary",
        "bibliography",
        "index",
        "acknowledgments",
        "dedication",
        "cover",
    }
)
#: Tokens that mean "this file is exactly one chapter".
_CHAPTER_TYPES: Final = frozenset(
    {"chapter", "prologue", "epilogue", "preface", "foreword", "introduction", "afterword"}
)
_PART_TYPES: Final = frozenset({"part", "volume", "division"})

_GUTENBERG_START = re.compile(r"\*\*\*\s*START OF (THE|THIS) PROJECT GUTENBERG", re.I)
_GUTENBERG_END = re.compile(r"\*\*\*\s*END OF (THE|THIS) PROJECT GUTENBERG", re.I)
#: A bare footnote marker: "[1]", "1", "*". Dropped when it is the whole text
#: of a link or superscript.
_NOTE_MARK = re.compile(r"^\[?\s*(\d{1,4}|[*†‡§])\s*\]?$")
_SCENE_BREAK = re.compile(r"^[\s*_\-—–·•~.]{1,20}$")
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_ZERO_WIDTH = re.compile("[\u200b\u200c\u200d\u2060\ufeff\u00ad]")
#: Stands in for ``<br>`` while text is extracted, so a real line break can be
#: told apart from the newlines a transcriber's editor wrapped the source at.
_LINE_BREAK: Final = "\u2028"
#: One entry of a table of contents: a chapter numeral, perhaps labelled.
_TOC_ENTRY = re.compile(r"^(chapter|part|book|volume)?[\s:.,]*[ivxlcdm\d]+[.,]?$", re.I)

#: A "chapter" shorter than this is a part title or an epigraph page that got
#: its own heading, not a chapter. Folded into its neighbour.
_MIN_CHAPTER_WORDS: Final = 40
#: Title given to what precedes the first chapter heading in an unmarked book.
FRONT_MATTER_TITLE: Final = "Front matter"


@dataclass(slots=True)
class ParsedBlock:
    kind: BlockKind
    text: str
    #: Heading level 1-6 for headings; 0 otherwise.
    level: int = 0


@dataclass(slots=True)
class ParsedChapter:
    title: str
    part_title: str = ""
    blocks: list[ParsedBlock] = field(default_factory=list)

    @property
    def word_count(self) -> int:
        return sum(word_count(b.text) for b in self.blocks)


@dataclass(slots=True)
class ParsedBook:
    title: str
    author: str
    language: str
    identifier: str
    description: str = ""
    rights: str = ""
    cover_href: str = ""
    chapters: list[ParsedChapter] = field(default_factory=list)

    @property
    def content_hash(self) -> str:
        """sha256 over titles and block texts in order. The identity of the text."""
        digest = hashlib.sha256()
        for chapter in self.chapters:
            digest.update(chapter.title.encode())
            for block in chapter.blocks:
                digest.update(b"\x1f")
                digest.update(block.text.encode())
            digest.update(b"\x1e")
        return digest.hexdigest()


class EpubParseError(ValueError):
    """The file is not an EPUB this parser can read. Never a network problem."""


# ── Canonical text ────────────────────────────────────────────


def canonical_text(raw: str) -> str:
    """The one normalisation offsets are measured against. Keep it boring.

    NFC so that a decomposed "é" and a precomposed one are one character; zero
    width and soft-hyphen code points removed because they are invisible and
    would make two visually identical taps disagree; whitespace collapsed to
    single spaces. Curly quotes and dashes are *kept* — they are the text.
    """
    text = unicodedata.normalize("NFC", raw)
    text = _ZERO_WIDTH.sub("", text)
    return " ".join(text.split())


def canonical_verse(raw: str) -> str:
    """Like :func:`canonical_text`, but one line per ``<br>``-separated line."""
    lines = [canonical_text(line) for line in raw.split(_LINE_BREAK)]
    return "\n".join(line for line in lines if line)


def word_count(text: str) -> int:
    return len(_WORD.findall(text))


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# ── Container and manifest ────────────────────────────────────


@dataclass(slots=True)
class _Manifest:
    spine: list[str]  # archive paths, in reading order
    metadata: ParsedBook


def _read_manifest(archive: zipfile.ZipFile) -> _Manifest:
    try:
        container = ET.fromstring(archive.read("META-INF/container.xml"))
    except KeyError as exc:
        raise EpubParseError("not an EPUB: META-INF/container.xml missing") from exc
    rootfile = container.find(".//c:rootfile", _CONTAINER_NS)
    if rootfile is None or not rootfile.get("full-path"):
        raise EpubParseError("container.xml names no rootfile")
    opf_path = rootfile.get("full-path", "")
    opf_dir = posixpath.dirname(opf_path)
    opf = ET.fromstring(archive.read(opf_path))

    def dc(name: str) -> str:
        node = opf.find(f".//dc:{name}", _OPF_NS)
        return canonical_text(node.text or "") if node is not None else ""

    items: dict[str, str] = {}
    cover_href = ""
    for item in opf.findall(".//opf:manifest/opf:item", _OPF_NS):
        item_id = item.get("id", "")
        items[item_id] = posixpath.normpath(posixpath.join(opf_dir, item.get("href", "")))
        if "cover-image" in (item.get("properties") or "").split():
            cover_href = items[item_id]
    if not cover_href:
        # EPUB2 convention: <meta name="cover" content="<item id>">.
        for meta in opf.findall(".//opf:metadata/opf:meta", _OPF_NS):
            if meta.get("name") == "cover" and meta.get("content") in items:
                cover_href = items[meta.get("content", "")]

    spine = [
        items[ref.get("idref", "")]
        for ref in opf.findall(".//opf:spine/opf:itemref", _OPF_NS)
        if ref.get("idref") in items and ref.get("linear", "yes") != "no"
    ]
    metadata = ParsedBook(
        title=dc("title"),
        author=dc("creator"),
        language=(dc("language") or "en")[:2].lower(),
        identifier=dc("identifier"),
        description=dc("description"),
        rights=dc("rights"),
        cover_href=cover_href,
    )
    return _Manifest(spine=spine, metadata=metadata)


# ── Block extraction ──────────────────────────────────────────


@dataclass(slots=True)
class _Section:
    """One spine document, classified and flattened."""

    href: str
    types: frozenset[str]
    blocks: list[ParsedBlock]

    @property
    def is_paratext(self) -> bool:
        return bool(self.types & _PARATEXT_TYPES)

    @property
    def is_chapter_file(self) -> bool:
        return bool(self.types & _CHAPTER_TYPES)

    @property
    def is_part_file(self) -> bool:
        return bool(self.types & _PART_TYPES) and not self.is_chapter_file


def _epub_types(tag: Tag) -> set[str]:
    return set(str(tag.get("epub:type") or "").split())


def _strip_noise(soup: BeautifulSoup) -> None:
    """Remove what is not prose before any text is read."""
    for name in ("script", "style", "figure", "svg", "table", "nav", "aside"):
        for tag in soup.find_all(name):
            tag.decompose()
    # An illustration's caption is not prose — Gutenberg's illustrated
    # editions even put it *inside* the chapter heading.
    for tag in soup.find_all(class_=re.compile(r"caption")):
        tag.decompose()
    for img in soup.find_all("img"):
        # An illustrated drop cap carries its letter as alt text ("W" for
        # "When…"); dropping the image would drop the letter. Longer alt text
        # describes a picture and is not part of the sentence.
        alt = str(img.get("alt") or "").strip()
        img.replace_with(alt if len(alt) <= 2 else "")
    for tag in soup.find_all(["a", "sup"]):
        if "noteref" in _epub_types(tag) or _NOTE_MARK.match(tag.get_text(strip=True) or ""):
            tag.decompose()
    # Gutenberg's newer EPUBs mark their own boilerplate; older ones are cut at
    # the START/END markers in :func:`_cut_gutenberg`.
    for tag in soup.find_all(id=re.compile(r"^pg-(header|footer)$")):
        tag.decompose()
    for tag in soup.find_all(class_=re.compile(r"pg-boilerplate|x-ebookmaker-pageno|toc")):
        tag.decompose()


def _blocks_of(tag: Tag) -> Iterator[ParsedBlock]:
    """Walk a document in order, yielding blocks and descending into wrappers."""
    for child in tag.children:
        if not isinstance(child, Tag):
            continue
        name = child.name or ""
        if name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            text = canonical_text(child.get_text(" "))
            if text:
                yield ParsedBlock(BlockKind.HEADING, text, level=int(name[1]))
        elif name == "p":
            yield from _paragraph(child, BlockKind.PARAGRAPH)
        elif name == "blockquote":
            # A quotation's own paragraphs, each as a quote block. Verse inside
            # a blockquote keeps its lines.
            inner = list(_blocks_of(child))
            if inner:
                for block in inner:
                    if block.kind is BlockKind.PARAGRAPH:
                        block.kind = BlockKind.QUOTE
                    yield block
            else:
                yield from _paragraph(child, BlockKind.QUOTE)
        elif name in {"li", "dd", "dt"}:
            yield from _paragraph(child, BlockKind.PARAGRAPH)
        elif name == "hr":
            continue
        else:
            # section, div, article, body, header, ol, ul, dl, span wrappers …
            yield from _blocks_of(child)


def _paragraph(tag: Tag, kind: BlockKind) -> Iterator[ParsedBlock]:
    for br in tag.find_all("br"):
        br.replace_with(_LINE_BREAK)
    raw = tag.get_text("")
    if _LINE_BREAK in raw.strip(" \n\t" + _LINE_BREAK):
        verse = canonical_verse(raw)
        if all(_SCENE_BREAK.match(line) for line in verse.split("\n")):
            return  # A block of asterisks is a scene break, not a poem.
        if "\n" in verse:
            yield ParsedBlock(BlockKind.VERSE, verse)
            return
    text = canonical_text(raw.replace(_LINE_BREAK, " "))
    if text and not _SCENE_BREAK.match(text) and not _looks_like_toc(text):
        yield ParsedBlock(kind, text)


def _looks_like_toc(text: str) -> bool:
    """A "paragraph" that is a run of chapter numerals is a table of contents."""
    entries = [e for e in re.split(r"[,;]\s*", text) if e.strip()]
    if len(entries) < 4:
        return False
    numerals = sum(1 for e in entries if _TOC_ENTRY.match(e.strip()))
    return numerals >= len(entries) * 0.8


def _parse_section(href: str, html: bytes) -> _Section:
    with warnings.catch_warnings():
        # The lenient HTML parser is the deliberate choice: Gutenberg files span
        # fifty years of hand-made markup, and an XML parser rejects the sloppy
        # ones outright. Scoped here, not module-wide, so importing this module
        # changes no warning filter anyone else relies on.
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(html, "lxml")
    _strip_noise(soup)
    body = soup.body or soup
    types: set[str] = _epub_types(body) if isinstance(body, Tag) else set()
    # Standard Ebooks put the type on the first <section>, not on <body>.
    for section in body.find_all("section", limit=3) if isinstance(body, Tag) else []:
        types |= _epub_types(section)
    return _Section(href=href, types=frozenset(types), blocks=list(_blocks_of(body)))


# ── Chapter segmentation ──────────────────────────────────────


def _cut_gutenberg(blocks: list[ParsedBlock]) -> list[ParsedBlock]:
    """Keep only what lies between the START and END markers, when present."""
    start = next((i for i, b in enumerate(blocks) if _GUTENBERG_START.search(b.text)), None)
    end = next((i for i, b in enumerate(blocks) if _GUTENBERG_END.search(b.text)), None)
    if start is None and end is None:
        return blocks
    lo = start + 1 if start is not None else 0
    hi = end if end is not None else len(blocks)
    return blocks[lo:hi]


def _chapter_heading_level(blocks: list[ParsedBlock]) -> int:
    """The heading level chapters are written at, for a source with no markup.

    The shallowest level that occurs at least twice: one ``h1`` is the book's
    title, and the many ``h2`` beneath it are the chapters. A book with no
    repeated heading level has no chapters this parser can find, and comes out
    as one.
    """
    counts = Counter(b.level for b in blocks if b.kind is BlockKind.HEADING)
    for level in sorted(counts):
        if counts[level] >= 2:
            return level
    return 0


def _content_key(text: str) -> str:
    return "".join(ch for ch in text.casefold() if ch.isalnum())


def _segment_by_heading(blocks: list[ParsedBlock], book_title: str) -> list[ParsedChapter]:
    level = _chapter_heading_level(blocks)
    chapters: list[ParsedChapter] = []
    current = ParsedChapter(title=FRONT_MATTER_TITLE if level else book_title)
    part_title = ""
    book_key = _content_key(book_title)
    for block in blocks:
        if block.kind is BlockKind.HEADING and level and block.level < level:
            # A shallower heading is a part — unless it is the book's own title
            # printed above the first chapter.
            if _content_key(block.text) != book_key:
                part_title = block.text
            continue
        if block.kind is BlockKind.HEADING and block.level == level:
            if current.blocks:
                chapters.append(current)
            current = ParsedChapter(title=block.text, part_title=part_title)
            continue
        current.blocks.append(block)
    if current.blocks:
        chapters.append(current)
    return chapters


def _segment(sections: list[_Section], book_title: str) -> list[ParsedChapter]:
    """Chapters from classified sections; by heading only where markup is silent."""
    if any(s.is_chapter_file for s in sections):
        chapters: list[ParsedChapter] = []
        part_title = ""
        for section in sections:
            if section.is_paratext:
                continue
            if section.is_part_file:
                heading = next((b for b in section.blocks if b.kind is BlockKind.HEADING), None)
                part_title = heading.text if heading else part_title
                continue
            if not section.blocks:
                continue
            title = ""
            body = section.blocks
            while body and body[0].kind is BlockKind.HEADING:
                # "I" then "The Arrival": a numeral and a name, joined.
                title = f"{title}: {body[0].text}" if title else body[0].text
                body = body[1:]
            chapters.append(ParsedChapter(title=title, part_title=part_title, blocks=body))
        return chapters

    stream = [b for s in sections if not s.is_paratext for b in s.blocks]
    return _segment_by_heading(_cut_gutenberg(stream), book_title)


def _fold_slivers(chapters: list[ParsedChapter]) -> list[ParsedChapter]:
    """Merge a heading-only or tiny "chapter" into the one after it.

    A part title the heading detector mistook for a chapter, or an epigraph
    page, becomes a heading block at the top of the next chapter rather than a
    forty-word chapter of its own in the table of contents.
    """
    first = chapters[0] if chapters else None
    if first and first.title == FRONT_MATTER_TITLE and first.word_count < _MIN_CHAPTER_WORDS:
        # A title page's credits ("by Lewis Carroll") are not the opening of
        # chapter one; a real preface is long enough to keep its own place.
        chapters = chapters[1:]
    folded: list[ParsedChapter] = []
    pending: ParsedChapter | None = None
    for chapter in chapters:
        if not any(b.kind is not BlockKind.HEADING for b in chapter.blocks):
            # Headings and nothing else — a title page's "by Lewis Carroll"
            # over "Edition 3.0". There is no prose to keep.
            continue
        if chapter.word_count < _MIN_CHAPTER_WORDS:
            pending = chapter if pending is None else _merge(pending, chapter)
            continue
        if pending is not None:
            chapter = _merge(pending, chapter)
            pending = None
        folded.append(chapter)
    if pending is not None:
        if folded:
            folded[-1] = _merge(folded[-1], pending)
        else:
            folded.append(pending)
    return folded


def _merge(head: ParsedChapter, tail: ParsedChapter) -> ParsedChapter:
    lead = [ParsedBlock(BlockKind.HEADING, head.title, level=2)] if head.title else []
    return ParsedChapter(
        title=tail.title or head.title,
        part_title=tail.part_title or head.part_title,
        blocks=[*lead, *head.blocks, *tail.blocks],
    )


def _number_untitled(chapters: list[ParsedChapter]) -> None:
    for i, chapter in enumerate(chapters, start=1):
        if not chapter.title:
            chapter.title = f"Chapter {i}"


# ── Entry point ───────────────────────────────────────────────


def parse_epub(data: bytes) -> ParsedBook:
    """Parse an EPUB (2 or 3) into chapters of canonical blocks.

    Raises :class:`EpubParseError` for anything that is not a readable EPUB.
    Never touches the network and never writes anything.
    """
    try:
        archive = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise EpubParseError("not a zip archive") from exc
    with archive:
        manifest = _read_manifest(archive)
        sections: list[_Section] = []
        for href in manifest.spine:
            try:
                html = archive.read(href)
            except KeyError:
                continue
            sections.append(_parse_section(href, html))

    book = manifest.metadata
    chapters = _fold_slivers(_segment(sections, book_title=book.title))
    _number_untitled(chapters)
    if not chapters:
        raise EpubParseError("no readable chapters found")
    book.chapters = chapters
    return book
