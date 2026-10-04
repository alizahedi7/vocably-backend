"""Phone and email normalisation — the one function every write and lookup shares."""

from __future__ import annotations

import pytest

from app.domain.enums import IdentifierType
from app.domain.services.identifiers import normalize, normalize_email, normalize_phone


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+989121234567", "+989121234567"),
        ("  +98 912 123 4567 ", "+989121234567"),
        ("+98-912-123-4567", "+989121234567"),
        ("+98 (912) 123.4567", "+989121234567"),
        ("00989121234567", "+989121234567"),
        # Typed on a Persian and on an Arabic keyboard: the same phone.
        ("+۹۸۹۱۲۱۲۳۴۵۶۷", "+989121234567"),
        ("+٩٨٩١٢١٢٣٤٥٦٧", "+989121234567"),
    ],
)
def test_one_phone_has_one_spelling(raw: str, expected: str) -> None:
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "09121234567",  # national format: the country code is never guessed
        "9121234567",
        "+0912123456",  # a country code cannot start with zero
        "+98912",  # too short
        "+9891212345678901",  # too long
        "+98912abc4567",
        "",
        "+",
    ],
)
def test_anything_short_of_a_full_international_number_is_refused(raw: str) -> None:
    assert normalize_phone(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ali@example.com", "ali@example.com"),
        ("  Ali@Example.COM ", "ali@example.com"),
        ("first.last+tag@mail.example.co.uk", "first.last+tag@mail.example.co.uk"),
    ],
)
def test_an_email_is_trimmed_and_lowercased_and_nothing_else(raw: str, expected: str) -> None:
    assert normalize_email(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "ali",
        "ali@",
        "@example.com",
        "ali@example",  # no dotted domain
        "ali@.com",
        "ali@example..com",
        "ali@@example.com",
        "ali smith@example.com",
        "ali@exam ple.com",
        "ali\u0000@example.com",
        "a" * 65 + "@example.com",  # local part over 64
        "ali@" + "d" * 320 + ".com",
    ],
)
def test_what_is_not_an_address_is_refused(raw: str) -> None:
    assert normalize_email(raw) is None


def test_normalize_dispatches_on_the_kind() -> None:
    assert normalize(IdentifierType.EMAIL, " A@B.io ") == "a@b.io"
    assert normalize(IdentifierType.PHONE, "+98 912 123 4567") == "+989121234567"
    # An email is not a phone, however valid it is as an email.
    assert normalize(IdentifierType.PHONE, "a@b.io") is None
