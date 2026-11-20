"""Tests for bnp.parser.

PRIVACY: this file is committed. Only structural facts about the real statement
are asserted here - never names, IBANs, addresses, card numbers or references.
"""
from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest

from bnp.parser import parse_amount, parse_statement

REPO_ROOT = Path(__file__).resolve().parent.parent

KNOWN_TYPES = (
    "Betaling met debetkaart",
    "Terugbetaling met debetkaart",
    "Geldopneming met debetkaart",
    "Overschrijving in euro",
    "Instant Europese overschrijving",
    "Wero overschrijving in euro",
    "Europese domiciliëring",
    "Uw doorlopende opdracht",
    "Storting van",
    "Mobiele betaling",
)

FORBIDDEN = (
    "Klantnr", "RPR Brussel", "Uittreksel", "Vorig saldo", "Huidig saldo",
    "Hello4You", "Paribas", "Tegenpartij", "Bedrag (EUR)", "P.DOC", "Tel.:", ".....",
)


def _all_pdfs() -> list[Path]:
    return sorted(REPO_ROOT.glob("input/*/*.pdf"))


def _example_pdf() -> Path:
    matches = _all_pdfs()
    if not matches:
        pytest.skip("no statement present in input/")
    return matches[-1]


# --- amount parser (invented values) ---------------------------------------

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("18,00 -", Decimal("-18.00")),
        ("18,00-", Decimal("-18.00")),
        ("1,05+", Decimal("1.05")),
        ("1.234,56+", Decimal("1234.56")),
        ("9.876,00 +", Decimal("9876.00")),
        ("1.234.567,89 -", Decimal("-1234567.89")),
        ("0,01 +", Decimal("0.01")),
        ("  42,50   -  ", Decimal("-42.50")),
    ],
)
def test_parse_amount(text, expected):
    assert parse_amount(text) == expected


@pytest.mark.parametrize("text", ["", "18,00", "18.00-", "12,5-", "abc", "1,000,00+", "18,00 *"])
def test_parse_amount_rejects_garbage(text):
    with pytest.raises(ValueError):
        parse_amount(text)


# --- your own statement, if present (structural only) ----------------------

@pytest.fixture(scope="module")
def statement():
    return parse_statement(_example_pdf())


def test_transaction_numbers_are_four_digit_and_consecutive(statement):
    numbers = [t.number for t in statement.transactions]
    assert numbers and all(re.fullmatch(r"\d{4}", n) for n in numbers)
    ints = sorted(int(n) for n in numbers)
    assert ints == list(range(ints[0], ints[0] + len(ints)))


def test_balances_reconcile(statement):
    total = sum((t.amount for t in statement.transactions), Decimal("0"))
    assert statement.previous_balance + total == statement.current_balance


def test_description_first_line_is_payment_type(statement):
    for t in statement.transactions:
        assert t.description_lines, t.number
        assert t.description_lines[0].startswith(KNOWN_TYPES), t.number


def test_no_header_or_footer_leakage(statement):
    for t in statement.transactions:
        for line in t.description_lines + t.counterparty_lines:
            assert line == line.strip() and line
            assert "  " not in line
            for word in FORBIDDEN:
                assert word not in line, (t.number, word)


def test_card_payments_have_no_counterparty(statement):
    card = [t for t in statement.transactions if t.description_lines[0].startswith("Betaling met debetkaart")]
    assert card
    for t in card:
        assert t.counterparty_lines == [], t.number


def test_transfers_have_counterparty(statement):
    transfers = [t for t in statement.transactions if t.description_lines[0].startswith("Overschrijving in euro")]
    for t in transfers:
        assert t.counterparty_lines, t.number


def test_valuta_and_amount_not_in_lines(statement):
    for t in statement.transactions:
        assert t.amount != 0
        for line in t.description_lines + t.counterparty_lines:
            # amounts end in ",dd+" / ",dd-"; none should leak into the text columns
            assert not re.search(r"\d,\d{2}\s*[+-]$", line), t.number
            assert not re.fullmatch(r"\d{2}-\d{2}", line), t.number


def test_booking_dates_near_statement_month(statement):
    year, month = map(int, re.search(r"-(\d{4})-(\d{1,2})\.pdf$", _example_pdf().name, re.I).groups())
    for t in statement.transactions:
        # a transaction can be booked on the statement of the neighbouring month
        assert abs((t.booking_date.year * 12 + t.booking_date.month) - (year * 12 + month)) <= 1, t.number


@pytest.mark.parametrize("pdf", _all_pdfs() or [None], ids=lambda p: p.name[-11:] if p else "none")
def test_every_statement_reconciles(pdf):
    if pdf is None:
        pytest.skip("no statements present in input/")
    s = parse_statement(pdf)
    assert s.transactions
    total = sum((t.amount for t in s.transactions), Decimal("0"))
    assert s.previous_balance + total == s.current_balance
    numbers = [int(t.number) for t in s.transactions]
    assert sorted(numbers) == list(range(min(numbers), max(numbers) + 1))


def test_missing_table_raises(tmp_path):
    pdf = tmp_path / "empty.pdf"
    # Minimal single blank page PDF.
    pdf.write_bytes(
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]>>endobj\n"
        b"trailer<</Root 1 0 R>>\n%%EOF\n"
    )
    with pytest.raises(ValueError):
        parse_statement(pdf)
