"""Build small EPUBs in memory, so parser and ingest tests need no fixtures.

Real books are hundreds of kilobytes to tens of megabytes and would have to be
downloaded or committed. These are a few hundred bytes each and shaped to
exercise exactly one rule of the parser at a time, in the two dialects it
reads: Standard Ebooks' semantic EPUB3, and Project Gutenberg's HTML.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from io import BytesIO

_XHTML = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>{title}</title></head>
<body{body_type}>{body}</body>
</html>
"""


@dataclass
class Doc:
    """One spine document. ``epub_type`` goes on a wrapping ``<section>``."""

    name: str
    body: str
    epub_type: str = ""
    #: Put the type on ``<body>`` instead of a section, as some EPUBs do.
    type_on_body: bool = False


@dataclass
class Book:
    title: str = "A Test Book"
    author: str = "Ann Author"
    language: str = "en-GB"
    identifier: str = "urn:test:book"
    rights: str = ""
    docs: list[Doc] = field(default_factory=list)
    cover: bool = False


def build_epub(book: Book) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?>'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        manifest, spine = [], []
        for i, doc in enumerate(book.docs):
            item_id = f"doc{i}"
            manifest.append(
                f'<item id="{item_id}" href="text/{doc.name}" media-type="application/xhtml+xml"/>'
            )
            spine.append(f'<itemref idref="{item_id}"/>')
            if doc.epub_type and not doc.type_on_body:
                body = f'<section epub:type="{doc.epub_type}">{doc.body}</section>'
            else:
                body = doc.body
            body_type = f' epub:type="{doc.epub_type}"' if doc.type_on_body else ""
            archive.writestr(
                f"OEBPS/text/{doc.name}",
                _XHTML.format(title=doc.name, body_type=body_type, body=body),
            )
        if book.cover:
            manifest.append(
                '<item id="cover" href="images/cover.jpg" media-type="image/jpeg" '
                'properties="cover-image"/>'
            )
            archive.writestr("OEBPS/images/cover.jpg", b"\xff\xd8\xff")
        rights = f"<dc:rights>{book.rights}</dc:rights>" if book.rights else ""
        archive.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f"<dc:title>{book.title}</dc:title><dc:creator>{book.author}</dc:creator>"
            f"<dc:language>{book.language}</dc:language>"
            f"<dc:identifier>{book.identifier}</dc:identifier>{rights}"
            f"</metadata><manifest>{''.join(manifest)}</manifest>"
            f"<spine>{''.join(spine)}</spine></package>",
        )
    return buffer.getvalue()


def prose(sentences: int = 12, word: str = "word") -> str:
    """A paragraph long enough to count as a chapter (≥ 40 words)."""
    return " ".join(f"This is {word} sentence number {n} of the chapter." for n in range(sentences))


def standard_ebooks_book(*, chapters: int = 3) -> Book:
    """Semantic EPUB3: paratext by name, one file per chapter."""
    docs = [
        Doc("titlepage.xhtml", "<h1>A Test Book</h1><p>By Ann Author.</p>", "titlepage"),
        Doc("imprint.xhtml", "<p>This ebook is the product of many hours.</p>", "imprint"),
    ]
    for n in range(1, chapters + 1):
        docs.append(
            Doc(
                f"chapter-{n}.xhtml",
                f"<h2>{'I' * n}</h2><h3>Title {n}</h3><p>{prose(word=f'c{n}')}</p>",
                "chapter",
            )
        )
    docs.append(Doc("uncopyright.xhtml", "<p>May you do good and not evil.</p>", "copyright-page"))
    return Book(docs=docs, cover=True, rights="Public domain in the USA.")


def gutenberg_book() -> Book:
    """One HTML file in Gutenberg's shape: boilerplate, title page, TOC, chapters."""
    body = (
        '<header class="pg-boilerplate" id="pg-header"><h2>The Project Gutenberg eBook</h2>'
        "<p>This eBook is for the use of anyone anywhere.</p></header>"
        "<p>*** START OF THE PROJECT GUTENBERG EBOOK A TEST BOOK ***</p>"
        "<h1>A Test Book</h1>"
        "<h2>by Ann Author</h2>"
        "<h4>THE MILLENNIUM EDITION</h4>"
        "<h2>Contents</h2>"
        '<p class="toc">CHAPTER I., II., III., IV., V.</p>'
        '<h2><img alt="" src="i1.jpg"/><span class="caption">A drawing.</span> CHAPTER I.</h2>'
        f'<p><img alt="W" src="w.png"/>HEN it began. {prose(word="one")}</p>'
        "<h2>CHAPTER II.</h2>"
        f"<p>{prose(word='two')}</p>"
        "<p>* * * * *</p>"
        f"<p>{prose(sentences=6, word='after-break')}</p>"
        "<h2>CHAPTER III.</h2>"
        f"<p>{prose(word='three')}</p>"
        "<p>*** END OF THE PROJECT GUTENBERG EBOOK A TEST BOOK ***</p>"
        '<footer id="pg-footer"><p>Updated editions will replace the previous one.</p></footer>'
    )
    return Book(docs=[Doc("book.xhtml", body)], identifier="http://www.gutenberg.org/11")
