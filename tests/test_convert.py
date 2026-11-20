"""Tests for convert.py.

PRIVACY: every name, IBAN and amount in the unit tests below is invented.
"""
from __future__ import annotations

import csv
import io
import re
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import convert  # noqa: E402
from bnp.models import RawTransaction, Row, Statement  # noqa: E402


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #

def tx(number, d, amount, seller="Fake Shop", desc=""):
    return RawTransaction(
        number=number,
        booking_date=d,
        description_lines=["Betaling met debetkaart", desc] if desc else ["Betaling met debetkaart"],
        counterparty_lines=[seller],
        amount=Decimal(amount),
    )


def stmt(prev, transactions, current=None):
    prev = Decimal(prev)
    if current is None:
        current = prev + sum((t.amount for t in transactions), Decimal("0"))
    return Statement(previous_balance=prev, current_balance=Decimal(current), transactions=transactions)


def fake_clean(raw: RawTransaction) -> Row:
    return Row(
        date=raw.booking_date,
        seller=raw.counterparty_lines[0] if raw.counterparty_lines else "",
        description=" ".join(raw.description_lines[1:]),
        amount=raw.amount,
    )


class FakeParser:
    """Returns the Statement registered for a PDF's file name."""

    def __init__(self, mapping: dict[str, Statement]):
        self.mapping = mapping
        self.calls: list[str] = []

    def __call__(self, path: Path) -> Statement:
        self.calls.append(path.name)
        return self.mapping[path.name]


def make_pdf(input_dir: Path, folder: str, name: str) -> Path:
    d = input_dir / folder
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_bytes(b"%PDF-fake")
    return p


def run(tmp_path, mapping):
    out = io.StringIO()
    code = convert.run(tmp_path / "input", tmp_path / "output",
                       parse=FakeParser(mapping), clean=fake_clean, out=out)
    return code, out.getvalue()


def read_csv(path: Path) -> list[list[str]]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.reader(f))


SIMPLE = stmt("100.00", [tx("0001", date(2024, 3, 5), "-12.50", "Bakkerij Testmans")])


# --------------------------------------------------------------------------- #
# Discovery / filenames
# --------------------------------------------------------------------------- #

def test_discover_years_only_four_digit_folders(tmp_path):
    inp = tmp_path / "input"
    for name in ("2023", "2024", "misc", "20245", "abcd"):
        (inp / name).mkdir(parents=True)
    (inp / "2022").write_text("not a dir")
    assert [y for y, _ in convert.discover_years(inp)] == ["2023", "2024"]


def test_non_matching_pdf_is_warned_and_skipped(tmp_path):
    inp = tmp_path / "input"
    make_pdf(inp, "2024", "ACC-EX-2024-03.pdf")
    make_pdf(inp, "2024", "random-scan.pdf")
    parser = FakeParser({"ACC-EX-2024-03.pdf": SIMPLE})
    out = io.StringIO()
    code = convert.run(inp, tmp_path / "output", parse=parser, clean=fake_clean, out=out)
    assert code == 0
    assert parser.calls == ["ACC-EX-2024-03.pdf"]
    assert "WARNING" in out.getvalue() and "random-scan.pdf" in out.getvalue()
    assert (tmp_path / "output" / "2024.csv").exists()


def test_filename_pattern_is_case_insensitive(tmp_path):
    make_pdf(tmp_path / "input", "2024", "ACC-EX-2024-03.PDF")
    code, _ = run(tmp_path, {"ACC-EX-2024-03.PDF": SIMPLE})
    assert code == 0


def test_single_digit_month_is_accepted(tmp_path):
    make_pdf(tmp_path / "input", "2024", "ACC-EX-2024-3.pdf")
    code, _ = run(tmp_path, {"ACC-EX-2024-3.pdf": SIMPLE})
    assert code == 0
    assert (tmp_path / "output" / "2024.csv").exists()


def test_padded_and_unpadded_same_month_is_duplicate(tmp_path):
    make_pdf(tmp_path / "input", "2024", "ACC-EX-2024-3.pdf")
    make_pdf(tmp_path / "input", "2024", "ACC-EX-2024-03.pdf")
    code, text = run(tmp_path, {"ACC-EX-2024-3.pdf": SIMPLE, "ACC-EX-2024-03.pdf": SIMPLE})
    assert code != 0
    assert "duplicate" in text


def test_folder_year_mismatch_is_error(tmp_path):
    make_pdf(tmp_path / "input", "2024", "ACC-EX-2023-03.pdf")
    code, text = run(tmp_path, {"ACC-EX-2023-03.pdf": SIMPLE})
    assert code != 0
    assert "ACC-EX-2023-03.pdf" in text and "ERROR" in text
    assert not (tmp_path / "output" / "2024.csv").exists()


def test_duplicate_month_is_error(tmp_path):
    make_pdf(tmp_path / "input", "2024", "A-EX-2024-03.pdf")
    make_pdf(tmp_path / "input", "2024", "B-EX-2024-03.pdf")
    code, text = run(tmp_path, {"A-EX-2024-03.pdf": SIMPLE, "B-EX-2024-03.pdf": SIMPLE})
    assert code != 0
    assert "duplicate" in text
    assert not (tmp_path / "output" / "2024.csv").exists()


# --------------------------------------------------------------------------- #
# Safety checks
# --------------------------------------------------------------------------- #

def test_balance_mismatch_keeps_existing_csv_and_fails(tmp_path):
    make_pdf(tmp_path / "input", "2024", "ACC-EX-2024-03.pdf")
    out_dir = tmp_path / "output"
    out_dir.mkdir()
    existing = out_dir / "2024.csv"
    existing.write_text("previous content\n", encoding="utf-8")

    bad = stmt("100.00", [tx("0001", date(2024, 3, 5), "-12.50")], current="90.00")
    code, text = run(tmp_path, {"ACC-EX-2024-03.pdf": bad})

    assert code != 0
    assert existing.read_text(encoding="utf-8") == "previous content\n"
    assert "ACC-EX-2024-03.pdf" in text
    assert "87.50" in text and "90.00" in text


def test_error_in_one_year_does_not_block_other_years(tmp_path):
    make_pdf(tmp_path / "input", "2023", "ACC-EX-2023-12.pdf")
    make_pdf(tmp_path / "input", "2024", "ACC-EX-2024-03.pdf")
    bad = stmt("0", [tx("0001", date(2023, 12, 1), "-1.00")], current="5.00")
    code, _ = run(tmp_path, {"ACC-EX-2023-12.pdf": bad, "ACC-EX-2024-03.pdf": SIMPLE})
    assert code != 0
    assert not (tmp_path / "output" / "2023.csv").exists()
    assert (tmp_path / "output" / "2024.csv").exists()


@pytest.mark.parametrize("field_name, kwargs", [
    ("seller", {"seller": "XY12 3456 7890 1234 Testperson"}),
    ("description", {"desc": "Fake transfer XY1234567890123456"}),
    ("description", {"desc": "Kaart 1234 56XX XXXX 7890 test"}),
    ("seller", {"seller": "Shop 123456XXXXXX7890"}),
    ("description", {"desc": "Bankreferentie: 0000000000"}),
])
def test_leak_check_blocks_year(tmp_path, field_name, kwargs):
    make_pdf(tmp_path / "input", "2024", "ACC-EX-2024-03.pdf")
    s = stmt("50.00", [tx("0001", date(2024, 3, 9), "-3.00", **kwargs)])
    code, text = run(tmp_path, {"ACC-EX-2024-03.pdf": s})
    assert code != 0
    assert not (tmp_path / "output" / "2024.csv").exists()
    assert "ACC-EX-2024-03.pdf" in text
    assert "2024-03-09" in text and field_name in text
    # the offending value itself is never printed
    for value in kwargs.values():
        assert value not in text
    assert "XY12" not in text and "7890" not in text


def test_leak_check_allows_normal_text():
    rows = [Row(date(2024, 1, 1), "Supermarkt Fictief 123", "Factuur 2024 nr 55", Decimal("-1")),
            # fuel-station style merchant code: IBAN-shaped but too few digits
            Row(date(2024, 1, 2), "FUEL XY001234 SOMEWHERE", "", Decimal("-1"))]
    assert convert.check_leaks("x.pdf", rows) == []


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #

def test_rows_sorted_across_months(tmp_path):
    make_pdf(tmp_path / "input", "2024", "ACC-EX-2024-03.pdf")
    make_pdf(tmp_path / "input", "2024", "ACC-EX-2024-04.pdf")
    march = stmt("0", [
        tx("0012", date(2024, 3, 20), "-2.00", "C"),
        tx("0011", date(2024, 3, 20), "-1.00", "B"),
        tx("0010", date(2024, 3, 2), "-3.00", "A"),
        # booked in April's date range but on March's statement
        tx("0013", date(2024, 4, 1), "-4.00", "D-march"),
    ])
    april = stmt("-10.00", [
        tx("0001", date(2024, 4, 1), "-5.00", "D-april"),
        tx("0002", date(2024, 3, 31), "-6.00", "Late"),
    ])
    code, _ = run(tmp_path, {"ACC-EX-2024-03.pdf": march, "ACC-EX-2024-04.pdf": april})
    assert code == 0
    sellers = [r[2] for r in read_csv(tmp_path / "output" / "2024.csv")[1:]]
    assert sellers == ["A", "B", "C", "Late", "D-march", "D-april"]


def test_csv_formatting(tmp_path):
    make_pdf(tmp_path / "input", "2024", "ACC-EX-2024-03.pdf")
    s = stmt("1000", [
        tx("0001", date(2024, 3, 1), "-18", "Shop, With Comma", 'Note "quoted"'),
        tx("0002", date(2024, 3, 2), "1234.5", "Werkgever Fictief NV", "Loon maart"),
        tx("0003", date(2024, 3, 3), "-0.005", "Rounding Test"),
        tx("0004", date(2024, 3, 4), "1234567.891", "Big Test"),
    ])
    code, _ = run(tmp_path, {"ACC-EX-2024-03.pdf": s})
    assert code == 0
    path = tmp_path / "output" / "2024.csv"
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8")
    assert text.splitlines()[0] == "date,amount,name,notes"
    assert '-18.00,"Shop, With Comma","Note ""quoted"""' in text
    rows = read_csv(path)
    assert rows[0] == ["date", "amount", "name", "notes"]
    assert rows[1] == ["2024-03-01", "-18.00", "Shop, With Comma", 'Note "quoted"']
    assert rows[2] == ["2024-03-02", "1234.50", "Werkgever Fictief NV", "Loon maart"]
    assert rows[3][1] in ("-0.01", "-0.00", "0.00")
    assert rows[4][1] == "1234567.89"
    for r in rows[1:]:
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", r[0])
        assert re.fullmatch(r"-?\d+\.\d{2}", r[1])


def test_format_amount():
    assert convert.format_amount(Decimal("-18")) == "-18.00"
    assert convert.format_amount(Decimal("1234.56")) == "1234.56"
    assert convert.format_amount(Decimal("0")) == "0.00"
    assert convert.format_amount(Decimal("-0.00")) == "0.00"


def test_rerun_is_idempotent(tmp_path):
    make_pdf(tmp_path / "input", "2024", "ACC-EX-2024-03.pdf")
    make_pdf(tmp_path / "input", "2024", "ACC-EX-2024-04.pdf")
    mapping = {
        "ACC-EX-2024-03.pdf": stmt("10", [tx("0001", date(2024, 3, 3), "-1.00", "X")]),
        "ACC-EX-2024-04.pdf": stmt("9", [tx("0001", date(2024, 4, 3), "5.25", "Y")]),
    }
    assert run(tmp_path, mapping)[0] == 0
    first = (tmp_path / "output" / "2024.csv").read_bytes()
    assert run(tmp_path, mapping)[0] == 0
    second = (tmp_path / "output" / "2024.csv").read_bytes()
    assert first == second
    assert len(read_csv(tmp_path / "output" / "2024.csv")) == 3
    # no temp files left behind
    assert sorted(p.name for p in (tmp_path / "output").iterdir()) == ["2024.csv"]


def test_summary_does_not_print_transaction_contents(tmp_path):
    make_pdf(tmp_path / "input", "2024", "ACC-EX-2024-03.pdf")
    s = stmt("0", [tx("0001", date(2024, 3, 1), "-7.77", "Secret Seller Name", "secret memo")])
    code, text = run(tmp_path, {"ACC-EX-2024-03.pdf": s})
    assert code == 0
    assert "1 statement" in text and "1 row" in text
    assert "Secret Seller" not in text and "secret memo" not in text and "7.77" not in text


def test_parse_exception_is_reported_as_year_error(tmp_path):
    make_pdf(tmp_path / "input", "2024", "ACC-EX-2024-03.pdf")

    def boom(path):
        raise ValueError("cannot read")

    out = io.StringIO()
    code = convert.run(tmp_path / "input", tmp_path / "output", parse=boom, clean=fake_clean, out=out)
    assert code != 0
    assert "ACC-EX-2024-03.pdf" in out.getvalue()
    assert not (tmp_path / "output" / "2024.csv").exists()


def test_main_cli_arguments(tmp_path, monkeypatch):
    make_pdf(tmp_path / "in", "2024", "ACC-EX-2024-03.pdf")
    parser = FakeParser({"ACC-EX-2024-03.pdf": SIMPLE})
    monkeypatch.setattr(convert, "_default_parse", lambda: parser)
    monkeypatch.setattr(convert, "_default_clean", lambda: fake_clean)
    code = convert.main(["--input", str(tmp_path / "in"), "--output", str(tmp_path / "out" / "nested")])
    assert code == 0
    assert (tmp_path / "out" / "nested" / "2024.csv").exists()


# --------------------------------------------------------------------------- #
# End-to-end on a real statement (skipped when unavailable)
# --------------------------------------------------------------------------- #

def _real_modules_available() -> bool:
    try:
        import bnp.clean  # noqa: F401
        import bnp.parser  # noqa: F401
    except Exception:
        return False
    return True


_REAL_PDFS = sorted((REPO / "input").glob("[0-9][0-9][0-9][0-9]/*.pdf"))


@pytest.mark.skipif(not _real_modules_available() or not _REAL_PDFS,
                    reason="needs bnp.parser, bnp.clean and a statement PDF in input/<year>/")
def test_end_to_end_real_statement(tmp_path):
    from bnp.parser import parse_statement

    pdf = _REAL_PDFS[-1]
    statement = parse_statement(pdf)
    inp = tmp_path / "input" / pdf.parent.name
    inp.mkdir(parents=True)
    # only one statement, so the expected totals are well-defined
    (inp / pdf.name).write_bytes(pdf.read_bytes())

    out = io.StringIO()
    code = convert.run(tmp_path / "input", tmp_path / "output", out=out)
    assert code == 0, out.getvalue()

    path = tmp_path / "output" / f"{pdf.parent.name}.csv"
    text = path.read_text(encoding="utf-8")
    rows = read_csv(path)
    assert rows[0] == ["date", "amount", "name", "notes"]
    data = rows[1:]
    assert len(data) == len(statement.transactions)
    assert sum(Decimal(r[1]) for r in data) == statement.current_balance - statement.previous_balance

    for r in data:
        assert convert.leak_reason(r[2]) is None and convert.leak_reason(r[3]) is None
    for word in ("Bankreferentie", "Klantnr", "Uittreksel"):
        assert word.lower() not in text.lower()
