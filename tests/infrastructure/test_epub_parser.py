"""The EPUB parser: what counts as reading matter, and what the text looks like.

Every rule here was found on a real book — Gutenberg's illustrated *Pride and
Prejudice* and *Alice*, and Standard Ebooks' *Pride and Prejudice* — and each
test isolates one of them on a synthetic file, so a regression names the rule
it broke.
"""

from __future__ import annotations

import unicodedata
import zipfile
from io import BytesIO

import pytest

from app.domain.enums import BlockKind
from app.infrastructure.books.epub import (
    FRONT_MATTER_TITLE,
    EpubParseError,
    canonical_text,
    parse_epub,
    text_hash,
    word_count,
)
from tests.epub_builder import (
    Book,
    Doc,
    build_epub,
    gutenberg_book,
    prose,
    standard_ebooks_book,
)


def _all_text(book_bytes: bytes) -> str:
    parsed = parse_epub(book_bytes)
    return " ".join(b.text for c in parsed.chapters for b in c.blocks)


# ── Metadata ──────────────────────────────────────────────────


def test_it_reads_the_manifest_metadata_and_the_cover() -> None:
    parsed = parse_epub(build_epub(standard_ebooks_book()))
    assert parsed.title == "A Test Book"
    assert parsed.author == "Ann Author"
    # "en-GB" is the text's language; the column holds ISO 639-1.
    assert parsed.language == "en"
    assert parsed.rights == "Public domain in the USA."
    assert parsed.cover_href == "OEBPS/images/cover.jpg"


# ── Standard Ebooks: semantic markup ──────────────────────────


def test_semantic_chapters_are_one_file_each_and_paratext_is_dropped() -> None:
    parsed = parse_epub(build_epub(standard_ebooks_book(chapters=3)))
    assert [c.title for c in parsed.chapters] == ["I: Title 1", "II: Title 2", "III: Title 3"]
    text = " ".join(b.text for c in parsed.chapters for b in c.blocks)
    for paratext in ("many hours", "do good and not evil", "By Ann Author"):
        assert paratext not in text


def test_a_part_file_names_the_chapters_that_follow_it() -> None:
    book = standard_ebooks_book(chapters=2)
    book.docs.insert(3, Doc("part-2.xhtml", "<h2>Volume Two</h2>", "part"))
    parsed = parse_epub(build_epub(book))
    assert [c.part_title for c in parsed.chapters] == ["", "Volume Two"]


def test_an_unknown_section_type_is_kept_rather_than_silently_lost() -> None:
    book = standard_ebooks_book(chapters=1)
    book.docs.insert(2, Doc("note.xhtml", f"<h2>A Note</h2><p>{prose()}</p>", "z3998:letter"))
    parsed = parse_epub(build_epub(book))
    assert "A Note" in [c.title for c in parsed.chapters]


def test_quotations_and_letters_become_quote_blocks() -> None:
    body = f"<h2>I</h2><p>{prose()}</p><blockquote><p>Dear Jane, I write in haste.</p></blockquote>"
    parsed = parse_epub(build_epub(Book(docs=[Doc("c.xhtml", body, "chapter")])))
    kinds = [b.kind for b in parsed.chapters[0].blocks]
    assert kinds == [BlockKind.PARAGRAPH, BlockKind.QUOTE]


# ── Project Gutenberg: markers and heuristics ─────────────────


def test_gutenberg_boilerplate_never_reaches_the_text() -> None:
    text = _all_text(build_epub(gutenberg_book()))
    for boilerplate in ("Project Gutenberg", "START OF", "END OF", "anyone anywhere", "Updated"):
        assert boilerplate not in text


def test_gutenberg_chapters_are_found_by_the_repeated_heading_level() -> None:
    parsed = parse_epub(build_epub(gutenberg_book()))
    assert [c.title for c in parsed.chapters] == ["CHAPTER I.", "CHAPTER II.", "CHAPTER III."]


def test_title_page_residue_and_the_table_of_contents_are_dropped() -> None:
    # "by Ann Author" and "Contents" are chapter-level headings with no prose
    # under them; the TOC is a paragraph of numerals. None is a chapter, and
    # none may open chapter one either.
    parsed = parse_epub(build_epub(gutenberg_book()))
    first = parsed.chapters[0]
    assert all("Ann Author" not in b.text for b in first.blocks)
    assert all("II., III." not in b.text for c in parsed.chapters for b in c.blocks)
    assert first.title != FRONT_MATTER_TITLE


def test_an_illustrated_drop_cap_keeps_its_letter_and_a_caption_is_removed() -> None:
    parsed = parse_epub(build_epub(gutenberg_book()))
    assert parsed.chapters[0].blocks[0].text.startswith("WHEN it began.")
    # The caption sat inside the chapter heading; the title must not carry it.
    assert parsed.chapters[0].title == "CHAPTER I."


def test_a_scene_break_of_asterisks_is_dropped() -> None:
    parsed = parse_epub(build_epub(gutenberg_book()))
    texts = [b.text for b in parsed.chapters[1].blocks]
    assert len(texts) == 2
    assert not any(set(t) <= set("* ") for t in texts)


def test_a_substantial_preface_before_the_first_chapter_keeps_its_own_entry() -> None:
    body = (
        "<p>*** START OF THE PROJECT GUTENBERG EBOOK X ***</p>"
        f"<h2>PREFACE.</h2><p>{prose(word='preface')}</p>"
        f"<h3>CHAPTER I.</h3><p>{prose()}</p><h3>CHAPTER II.</h3><p>{prose()}</p>"
    )
    parsed = parse_epub(build_epub(Book(docs=[Doc("b.xhtml", body)])))
    # The preface is at a shallower level than the chapters, so it is a part
    # heading over prose that precedes chapter one — "Front matter".
    assert parsed.chapters[0].title == FRONT_MATTER_TITLE
    assert "preface" in parsed.chapters[0].blocks[0].text


def test_a_book_with_no_repeated_heading_is_one_chapter_named_for_the_book() -> None:
    body = f"<h1>A Test Book</h1><p>{prose()}</p><p>{prose(word='more')}</p>"
    parsed = parse_epub(build_epub(Book(docs=[Doc("b.xhtml", body)])))
    assert [c.title for c in parsed.chapters] == ["A Test Book"]


def test_a_sliver_chapter_is_folded_into_the_next_one() -> None:
    body = (
        "<h2>CHAPTER I.</h2><p>Just a few words here.</p>"
        f"<h2>CHAPTER II.</h2><p>{prose()}</p><h2>CHAPTER III.</h2><p>{prose()}</p>"
    )
    parsed = parse_epub(build_epub(Book(docs=[Doc("b.xhtml", body)])))
    assert [c.title for c in parsed.chapters] == ["CHAPTER II.", "CHAPTER III."]
    # The sliver's heading and prose survive, at the top of the next chapter.
    assert [b.text for b in parsed.chapters[0].blocks[:2]] == [
        "CHAPTER I.",
        "Just a few words here.",
    ]


# ── Blocks and canonical text ─────────────────────────────────


def test_only_br_makes_verse_and_source_newlines_are_whitespace() -> None:
    body = (
        f"<h2>I</h2><p>{prose()}</p>"
        "<p>A paragraph the transcriber\nwrapped at\nseventy columns.</p>"
        "<p>How doth the little crocodile<br/>Improve his shining tail,<br/>And pour</p>"
    )
    parsed = parse_epub(build_epub(Book(docs=[Doc("c.xhtml", body, "chapter")])))
    wrapped, verse = parsed.chapters[0].blocks[1:]
    assert wrapped.kind is BlockKind.PARAGRAPH
    assert wrapped.text == "A paragraph the transcriber wrapped at seventy columns."
    assert verse.kind is BlockKind.VERSE
    assert verse.text == "How doth the little crocodile\nImprove his shining tail,\nAnd pour"


def test_footnote_markers_are_removed_from_the_text() -> None:
    body = (
        f"<h2>I</h2><p>{prose()}</p>"
        '<p>She laughed.<a epub:type="noteref" href="#n1">1</a> Then she left.<sup>[2]</sup></p>'
    )
    parsed = parse_epub(build_epub(Book(docs=[Doc("c.xhtml", body, "chapter")])))
    assert parsed.chapters[0].blocks[1].text == "She laughed. Then she left."


def test_canonical_text_is_nfc_with_invisible_characters_removed() -> None:
    decomposed = unicodedata.normalize("NFD", "café")
    assert canonical_text(f"  {decomposed}\u200b  au\u00adlait \n ") == "café aulait"
    # Curly quotes and dashes are the text, and are kept.
    assert canonical_text("“Yes—no,” she said.") == "“Yes—no,” she said."


def test_word_count_and_hash_are_stable_functions_of_the_text() -> None:
    assert word_count("It’s a well-known truth, isn’t it?") == 9
    assert text_hash("abc") == text_hash("abc") != text_hash("abd")


def test_the_content_hash_identifies_the_text_and_nothing_else() -> None:
    first = parse_epub(build_epub(standard_ebooks_book()))
    again = parse_epub(build_epub(standard_ebooks_book()))
    assert first.content_hash == again.content_hash
    edited = standard_ebooks_book()
    edited.docs[2].body = edited.docs[2].body.replace("c1 sentence", "c1 line")
    assert parse_epub(build_epub(edited)).content_hash != first.content_hash


# ── Refusals ──────────────────────────────────────────────────


def test_a_file_that_is_not_a_zip_is_refused() -> None:
    with pytest.raises(EpubParseError, match="not a zip"):
        parse_epub(b"<html>Your download has started</html>")


def test_a_zip_without_a_container_is_refused() -> None:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "hello")
    with pytest.raises(EpubParseError, match="container.xml"):
        parse_epub(buffer.getvalue())


def test_an_epub_with_no_prose_is_refused() -> None:
    book = Book(docs=[Doc("t.xhtml", "<h1>Only a title</h1>", "titlepage")])
    with pytest.raises(EpubParseError, match="no readable chapters"):
        parse_epub(build_epub(book))
