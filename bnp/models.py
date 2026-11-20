"""Shared data structures between the parser, the cleaner and the CLI.

This file is the contract between modules - do not change field names or types
without updating every module that uses them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass
class RawTransaction:
    """One transaction block as laid out on the statement, before any cleaning.

    Produced by ``bnp.parser.parse_statement``; consumed by ``bnp.clean.to_row``.
    Lines are kept verbatim (whitespace-trimmed) so the cleaner decides what to keep.
    """

    number: str                      # 4-digit sequence number, e.g. "0678"
    booking_date: date               # date from the grey group header above the block
    description_lines: list[str]     # left "Type verrichting / omschrijving" column, top to bottom
                                     # (first line is the payment type, e.g. "Betaling met debetkaart")
    counterparty_lines: list[str] = field(default_factory=list)  # "Tegenpartij" column, top to bottom
    amount: Decimal = Decimal("0")   # signed: expenses negative, income positive


@dataclass
class Statement:
    """One parsed statement PDF."""

    previous_balance: Decimal        # "Vorig saldo op ..." (signed)
    current_balance: Decimal         # "Huidig saldo op ..." (signed)
    transactions: list[RawTransaction]


@dataclass
class Row:
    """One anonymised output CSV row."""

    date: date
    seller: str
    description: str
    amount: Decimal
