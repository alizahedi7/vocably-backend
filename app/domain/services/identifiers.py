"""Phone numbers and email addresses, as the strings an account is found by.

**This is the only place either is normalised.** A unique index compares
bytes, so two spellings of one number are two identifiers unless every write
and every lookup goes through the same function — which is how one phone ends
up on two accounts.
"""

from __future__ import annotations

import re
import unicodedata

from app.domain.enums import IdentifierType

#: E.164: a plus, a non-zero first digit, 8 to 15 digits in all. ASCII digits
#: spelled out rather than ``\d``, which also matches the Persian and Arabic
#: ones this module has already folded away.
_E164 = re.compile(r"\+[1-9][0-9]{7,14}")
#: What people put between the digits. Removed, never interpreted.
_PHONE_SEPARATORS = re.compile(r"[\s\-().]")

#: ``users.email`` is ``String(320)``: 64 for the local part, 255 for the
#: domain, and the ``@``.
MAX_EMAIL_LENGTH = 320
_MAX_LOCAL_LENGTH = 64
_MAX_DOMAIN_LENGTH = 255
#: A shape check, not RFC 5322: one ``@``, something before it, and a dotted
#: domain with no empty label. Whether the mailbox exists is answered by the
#: code we send to it, which is the only test that means anything.
_EMAIL = re.compile(r"[^@\s]+@[^@\s.]+(?:\.[^@\s.]+)+")


def normalize_phone(raw: str) -> str | None:
    """``raw`` as E.164, or ``None`` if it is not a full international number.

    Separators are dropped, a leading ``00`` becomes ``+``, and digits typed on
    a Persian or Arabic keyboard become ASCII — ``+۹۸۹۱۲…`` and ``+98912…`` are
    one phone and must be one row.

    A national number (``0912…``) is refused rather than completed: guessing
    the country code would attach a stranger's number to the account whenever
    the guess was wrong.
    """
    compact = _PHONE_SEPARATORS.sub("", _ascii_digits(raw))
    if compact.startswith("00"):
        compact = "+" + compact[2:]
    return compact if _E164.fullmatch(compact) else None


def normalize_email(raw: str) -> str | None:
    """``raw`` trimmed and lowercased, or ``None`` if it is not an address.

    Lowercased whole. The local part is case-sensitive on paper and on no
    mailbox anyone uses, and treating ``Ali@x.com`` and ``ali@x.com`` as two
    people would let one inbox verify two accounts.

    Nothing cleverer than that: stripping Gmail's dots or a ``+tag`` would
    merge addresses that other providers deliver to different people.
    """
    email = raw.strip().lower()
    if len(email) > MAX_EMAIL_LENGTH or not email.isprintable() or not _EMAIL.fullmatch(email):
        return None
    local, _, domain = email.rpartition("@")
    if len(local) > _MAX_LOCAL_LENGTH or len(domain) > _MAX_DOMAIN_LENGTH:
        return None
    return email


def normalize(kind: IdentifierType, raw: str) -> str | None:
    return normalize_email(raw) if kind is IdentifierType.EMAIL else normalize_phone(raw)


def _ascii_digits(text: str) -> str:
    return "".join(
        str(unicodedata.decimal(ch)) if ch.isdecimal() and not ch.isascii() else ch for ch in text
    )
