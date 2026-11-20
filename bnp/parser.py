"""Parse Hello bank! / BNP Paribas Fortis statement PDFs into ``Statement`` objects.

Words are read with their positions (``page.extract_words``) and assigned to
table columns by x-coordinate. Column boundaries are derived per page from the
table header words ("Nr.", "Type", "Tegenpartij", "Datum", "Bedrag"), so the
description and counterparty columns never get mixed even though they sit on
the same text line.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pdfplumber

from bnp.models import RawTransaction, Statement

__all__ = ["parse_statement", "parse_amount"]

_LINE_TOLERANCE = 2.0       # words whose tops differ less than this are on one line
_COLUMN_SLACK = 3.0         # words may start this far left of a header word

_NUMBER_RE = re.compile(r"^\d{4}$")
_BOOKING_DATE_RE = re.compile(r"^\d{2}-\d{2}-\d{4}$")
_VALUTA_RE = re.compile(r"^\d{2}-\d{2}$")
_DOTS_RE = re.compile(r"^\.{5,}$")
_AMOUNT_RE = re.compile(r"^(\d{1,3}(?:\.\d{3})*|\d+),(\d{2})\s*([+-])$")
_FOOTER_MARKERS = ("Warandeberg", "RPR")


def parse_amount(text: str) -> Decimal:
    """Convert a statement amount such as ``"1.234,56+"`` or ``"18,00 -"`` to a signed Decimal.

    ``.`` is the thousands separator, ``,`` the decimal separator and the trailing
    sign (optionally preceded by whitespace) gives the direction: ``-`` is negative.
    """
    cleaned = " ".join(text.split())
    m = _AMOUNT_RE.match(cleaned)
    if not m:
        raise ValueError(f"Unrecognised amount format: {text!r}")
    integer, cents, sign = m.groups()
    try:
        value = Decimal(f"{integer.replace('.', '')}.{cents}")
    except InvalidOperation as exc:  # pragma: no cover - regex already guarantees digits
        raise ValueError(f"Unrecognised amount format: {text!r}") from exc
    return -value if sign == "-" else value


@dataclass
class _Columns:
    desc_x: float       # left edge of "Type verrichting / omschrijving"
    cp_x: float         # left edge of "Tegenpartij"
    valuta_x: float     # left edge of "Valuta Datum"
    amount_x: float     # boundary between valuta and amount columns

    def classify(self, x0: float) -> str:
        if x0 < self.desc_x - _COLUMN_SLACK:
            return "nr"
        if x0 < self.cp_x - _COLUMN_SLACK:
            return "desc"
        if x0 < self.valuta_x - _COLUMN_SLACK:
            return "cp"
        if x0 < self.amount_x:
            return "valuta"
        return "amount"


def _group_lines(words: list[dict]) -> list[list[dict]]:
    """Group words into visual lines (sorted top to bottom, words left to right)."""
    lines: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if lines and abs(w["top"] - lines[-1][0]["top"]) < _LINE_TOLERANCE:
            lines[-1].append(w)
        else:
            lines.append([w])
    for line in lines:
        line.sort(key=lambda w: w["x0"])
    return lines


def _text(words: list[dict]) -> str:
    return " ".join(w["text"] for w in words).strip()


def _find_header(lines: list[list[dict]]) -> tuple[int, _Columns] | None:
    """Locate the table header line and derive column boundaries from it."""
    for i, line in enumerate(lines):
        by_text = {w["text"]: w for w in line}
        if not {"Tegenpartij", "Bedrag", "Datum", "Type"} <= by_text.keys():
            continue
        datum, bedrag = by_text["Datum"], by_text["Bedrag"]
        cols = _Columns(
            desc_x=by_text["Type"]["x0"],
            cp_x=by_text["Tegenpartij"]["x0"],
            valuta_x=datum["x0"],
            amount_x=(datum["x1"] + bedrag["x0"]) / 2,
        )
        return i, cols
    return None


def _footer_top(lines: list[list[dict]], page_height: float) -> float:
    """Top y of the repeated page footer (bank address block)."""
    for line in lines:
        if any(w["text"] in _FOOTER_MARKERS for w in line):
            return line[0]["top"] - _LINE_TOLERANCE
    return page_height * 0.94


def _is_balance_line(text: str) -> bool:
    return text.startswith("Vorig saldo") or text.startswith("Huidig saldo")


def _find_balance(all_lines: list[list[dict]], label: str) -> Decimal:
    for line in all_lines:
        text = _text(line)
        if not text.startswith(label):
            continue
        tokens = text.split()
        # "<label> op DD-MM-YYYY <amount> <sign>" -> amount follows the date
        for idx, tok in enumerate(tokens):
            if _BOOKING_DATE_RE.match(tok):
                return parse_amount(" ".join(tokens[idx + 1:]))
        raise ValueError(f"Could not read balance from line starting with {label!r}")
    raise ValueError(f"Balance line {label!r} not found in statement")


def parse_statement(path: str | Path) -> Statement:
    """Parse one statement PDF into a ``Statement`` with its raw transactions."""
    path = Path(path)
    transactions: list[RawTransaction] = []
    all_lines: list[list[dict]] = []
    found_table = False
    booking_date: date | None = None
    current: RawTransaction | None = None
    current_has_amount = False

    def finish() -> None:
        nonlocal current, current_has_amount
        if current is not None:
            if not current_has_amount:
                raise ValueError(f"{path.name}: transaction {current.number} has no amount")
            transactions.append(current)
        current = None
        current_has_amount = False

    with pdfplumber.open(path) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            lines = _group_lines(page.extract_words())
            all_lines.extend(lines)
            header = _find_header(lines)
            if header is None:
                continue
            found_table = True
            header_idx, cols = header
            footer_top = _footer_top(lines, page.height)

            for line in lines[header_idx + 1:]:
                if line[0]["top"] >= footer_top:
                    break
                text = _text(line)
                if _is_balance_line(text):
                    break  # end of the table on this page ("Vorig saldo" box on last page)
                if _DOTS_RE.match(text.replace(" ", "")):
                    continue

                parts: dict[str, list[dict]] = {"nr": [], "desc": [], "cp": [], "valuta": [], "amount": []}
                for w in line:
                    parts[cols.classify(w["x0"])].append(w)
                nr_text = _text(parts["nr"])

                # Grey bar: booking date alone at the left.
                if _BOOKING_DATE_RE.match(nr_text) and not any(parts[k] for k in ("desc", "cp", "valuta", "amount")):
                    finish()
                    booking_date = datetime.strptime(nr_text, "%d-%m-%Y").date()
                    continue

                if _NUMBER_RE.match(nr_text):
                    finish()
                    if booking_date is None:
                        raise ValueError(f"{path.name}: transaction {nr_text} appears before any booking date")
                    current = RawTransaction(
                        number=nr_text, booking_date=booking_date,
                        description_lines=[], counterparty_lines=[],
                    )
                elif nr_text:
                    raise ValueError(
                        f"{path.name} page {page_no}: unexpected text in Nr. column: {nr_text!r}"
                    )
                elif current is None:
                    raise ValueError(
                        f"{path.name} page {page_no}: table line outside any transaction: {text!r}"
                    )

                if parts["desc"]:
                    current.description_lines.append(_text(parts["desc"]))
                if parts["cp"]:
                    current.counterparty_lines.append(_text(parts["cp"]))
                valuta_text = _text(parts["valuta"])
                if valuta_text and not _VALUTA_RE.match(valuta_text):
                    raise ValueError(
                        f"{path.name}: unexpected valuta date {valuta_text!r} in transaction {current.number}"
                    )
                if parts["amount"]:
                    if current_has_amount:
                        raise ValueError(f"{path.name}: transaction {current.number} has two amounts")
                    current.amount = parse_amount(_text(parts["amount"]))
                    current_has_amount = True
        # A transaction may continue on the next page, so only close it at the very end.
        finish()

    if not found_table:
        raise ValueError(f"{path.name}: transaction table header not found")
    previous = _find_balance(all_lines, "Vorig saldo")
    current_balance = _find_balance(all_lines, "Huidig saldo")
    return Statement(previous_balance=previous, current_balance=current_balance, transactions=transactions)
