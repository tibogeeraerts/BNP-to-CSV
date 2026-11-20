"""Convert Hello bank! statement PDFs in input/<year>/ into output/<year>.csv.

Usage:
    python convert.py [--input DIR] [--output DIR]

Each run rebuilds every year's CSV from all statement PDFs in that year's
folder. A year with any error (bad filename year, duplicate month, balance
mismatch, possible data leak, parse failure) is reported and its CSV is left
untouched; the script then exits non-zero.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Callable, Iterable

from bnp.models import RawTransaction, Row, Statement

SCRIPT_DIR = Path(__file__).resolve().parent

YEAR_DIR_RE = re.compile(r"^\d{4}$")
# The bank names files "...-EX-2026-1.pdf" .. "...-EX-2026-12.pdf" (month not zero-padded).
STATEMENT_NAME_RE = re.compile(r"-(\d{4})-(\d{1,2})\.pdf$", re.IGNORECASE)

IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[0-9A-Z]{4}){2,7}")
MASKED_CARD_RE = re.compile(r"\d{4}\s?\d{2}XX\s?XXXX\s?\d{4}")
FORBIDDEN_SUBSTRINGS = ("Bankreferentie",)

# Column names and order of Sure's transaction import template, so Sure maps them automatically.
CSV_HEADER = ["date", "amount", "name", "notes"]

ParseFn = Callable[[Path], Statement]
CleanFn = Callable[[RawTransaction], Row]


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #

def discover_years(input_dir: Path) -> list[tuple[str, Path]]:
    """Return (year, folder) for every 4-digit subfolder of input_dir, sorted."""
    if not input_dir.is_dir():
        return []
    return sorted(
        (p.name, p) for p in input_dir.iterdir()
        if p.is_dir() and YEAR_DIR_RE.match(p.name)
    )


@dataclass
class StatementFile:
    path: Path
    year: int
    month: int


def find_statements(year: str, year_dir: Path) -> tuple[list[StatementFile], list[str], list[str]]:
    """Find statement PDFs in a year folder.

    Returns (statements sorted by month, warnings, errors).
    """
    statements: list[StatementFile] = []
    warnings: list[str] = []
    errors: list[str] = []
    seen: dict[int, Path] = {}

    pdfs = sorted(p for p in year_dir.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")
    for pdf in pdfs:
        m = STATEMENT_NAME_RE.search(pdf.name)
        if not m:
            warnings.append(f"{pdf.name}: filename does not end in -YYYY-M.pdf or -YYYY-MM.pdf, skipped")
            continue
        file_year, month = int(m.group(1)), int(m.group(2))
        if not 1 <= month <= 12:
            errors.append(f"{pdf.name}: invalid month {month:02d} in filename")
            continue
        if str(file_year) != year:
            errors.append(f"{pdf.name}: filename year {file_year} does not match folder {year}")
            continue
        if month in seen:
            errors.append(
                f"{pdf.name}: duplicate statement for {file_year}-{month:02d} "
                f"(also {seen[month].name})"
            )
            continue
        seen[month] = pdf
        statements.append(StatementFile(pdf, file_year, month))

    statements.sort(key=lambda s: s.month)
    return statements, warnings, errors


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #

def check_balance(pdf_name: str, statement: Statement) -> str | None:
    """Previous balance + sum of transactions must equal current balance exactly."""
    total = sum((t.amount for t in statement.transactions), Decimal("0"))
    computed = statement.previous_balance + total
    if computed != statement.current_balance:
        return (
            f"{pdf_name}: balance mismatch - previous balance {statement.previous_balance} "
            f"+ transactions {total} = {computed}, but statement says current balance "
            f"{statement.current_balance}"
        )
    return None


def leak_reason(text: str) -> str | None:
    """Return a short label for what kind of sensitive data the text contains, if any."""
    # Real IBANs carry at least 10 digits; merchant codes like "XY001234 SOMEWHERE" don't.
    if any(sum(c.isdigit() for c in m.group()) >= 10 for m in IBAN_RE.finditer(text)):
        return "IBAN-like number"
    if MASKED_CARD_RE.search(text):
        return "masked card number"
    for s in FORBIDDEN_SUBSTRINGS:
        if s.lower() in text.lower():
            return f"'{s}'"
    return None


def check_leaks(pdf_name: str, rows: Iterable[Row]) -> list[str]:
    """Report rows whose seller/description look like they leak sensitive data.

    The offending value itself is never included in the message.
    """
    errors = []
    for row in rows:
        for field_name in ("seller", "description"):
            reason = leak_reason(getattr(row, field_name) or "")
            if reason:
                errors.append(
                    f"{pdf_name}: row dated {row.date.isoformat()} - {field_name} "
                    f"contains a {reason}"
                )
    return errors


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #

def format_amount(amount: Decimal) -> str:
    """Two decimals, '.' separator, no thousands separator, '-' for negatives."""
    q = Decimal(amount).quantize(Decimal("0.01"))
    if q == 0:
        q = abs(q)  # avoid "-0.00"
    return f"{q:.2f}"


def csv_safe(text: str) -> str:
    """Prefix ' to text a spreadsheet would run as a formula (CSV injection)."""
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def write_csv_atomic(path: Path, rows: Iterable[Row]) -> None:
    """Write rows to path via a temp file in the same directory + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=".csv.tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, lineterminator="\n")
            writer.writerow(CSV_HEADER)
            for r in rows:
                writer.writerow([r.date.isoformat(), format_amount(r.amount), csv_safe(r.seller), csv_safe(r.description)])
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------- #
# Per-year processing
# --------------------------------------------------------------------------- #

@dataclass
class YearResult:
    year: str
    statements: int = 0
    rows: int = 0
    written: bool = False
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _sort_number(number: str) -> tuple[int, int | str]:
    return (0, int(number)) if number.isdigit() else (1, number)


def process_year(year: str, year_dir: Path, output_dir: Path,
                 parse: ParseFn, clean: CleanFn) -> YearResult:
    result = YearResult(year)
    files, result.warnings, result.errors = find_statements(year, year_dir)

    # (date, month, transaction number, original position) -> row
    keyed: list[tuple[tuple, Row]] = []
    for sf in files:
        name = sf.path.name
        try:
            statement = parse(sf.path)
        except Exception as e:  # noqa: BLE001 - report and continue with other PDFs
            result.errors.append(f"{name}: could not parse statement ({type(e).__name__}: {e})")
            continue

        err = check_balance(name, statement)
        if err:
            result.errors.append(err)
            continue

        rows: list[tuple[RawTransaction, Row]] = []
        try:
            for raw in statement.transactions:
                rows.append((raw, clean(raw)))
        except Exception as e:  # noqa: BLE001
            result.errors.append(f"{name}: could not clean transactions ({type(e).__name__})")
            continue

        leaks = check_leaks(name, (row for _, row in rows))
        if leaks:
            result.errors.extend(leaks)
            continue

        result.statements += 1
        for i, (raw, row) in enumerate(rows):
            keyed.append(((row.date, sf.month, _sort_number(raw.number), i), row))

    if result.errors:
        return result
    if not files:
        result.warnings.append("no statement PDFs found, nothing written")
        return result

    keyed.sort(key=lambda kr: kr[0])
    sorted_rows = [row for _, row in keyed]
    write_csv_atomic(output_dir / f"{year}.csv", sorted_rows)
    result.rows = len(sorted_rows)
    result.written = True
    return result


def _default_parse() -> ParseFn:
    from bnp.parser import parse_statement
    return parse_statement


def _default_clean() -> CleanFn:
    from bnp.clean import to_row
    return to_row


def run(input_dir: Path, output_dir: Path,
        parse: ParseFn | None = None, clean: CleanFn | None = None,
        out=None) -> int:
    """Convert all years. Returns the process exit code (0 = all good)."""
    out = out or sys.stdout
    parse = parse or _default_parse()
    clean = clean or _default_clean()

    years = discover_years(input_dir)
    if not years:
        print(f"No year folders (YYYY) found in {input_dir}", file=out)
        return 1

    failed = False
    for year, year_dir in years:
        result = process_year(year, year_dir, output_dir, parse, clean)
        for w in result.warnings:
            print(f"[{year}] WARNING: {w}", file=out)
        if result.errors:
            failed = True
            print(f"[{year}] FAILED - {output_dir / (year + '.csv')} left untouched:", file=out)
            for e in result.errors:
                print(f"[{year}]   ERROR: {e}", file=out)
        elif result.written:
            print(
                f"[{year}] OK: {result.statements} statement(s), {result.rows} row(s) "
                f"-> {output_dir / (year + '.csv')}",
                file=out,
            )
    if failed:
        print("Finished with errors.", file=out)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Convert Hello bank! statement PDFs to per-year CSVs.")
    ap.add_argument("--input", type=Path, default=SCRIPT_DIR / "input",
                    help="folder containing <year>/ subfolders with PDFs (default: ./input)")
    ap.add_argument("--output", type=Path, default=SCRIPT_DIR / "output",
                    help="folder for <year>.csv files (default: ./output)")
    args = ap.parse_args(argv)
    return run(args.input, args.output)


if __name__ == "__main__":
    sys.exit(main())
