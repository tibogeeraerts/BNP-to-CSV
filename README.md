# BNP pipeline

Turn your monthly **Hello bank! / BNP Paribas Fortis** account statement PDFs
into clean, anonymised CSV files that you can import into a budgeting app
(such as [Sure](https://github.com/we-promise/sure)) or hand to an AI for
budget analysis. Your IBAN, address and card numbers stay out of the files.

Everything runs locally on your machine: no data is uploaded anywhere.

```
input/2026/<account>-EX-2026-10.pdf  ──►  convert.py  ──►  output/2026.csv
```

## What you get

One CSV per year, with only four columns:

| Column   | Content                                                                |
|----------|------------------------------------------------------------------------|
| `date`   | Booking date, `YYYY-MM-DD`                                             |
| `amount` | Signed amount in EUR, `.` as decimal separator, expenses negative      |
| `name`   | Merchant name, or counterparty name for transfers / direct debits      |
| `notes`  | Payment message with reference codes removed (empty for card payments) |

```csv
date,amount,name,notes
2026-10-01,-750.00,Jan Voorbeeld,Maandelijkse huur
2026-10-03,-23.45,SOME SHOP NV,
2026-10-28,1234.56,Werkgever Fictief NV,Loon oktober
```

Everything else on the statement (account holder, customer number, IBANs,
BICs, addresses, card numbers, bank references, mandate numbers) is never
written to the output. Before writing a file, the script also checks every
row for anything that looks like an IBAN or card number and refuses to write
that year if it finds one. A `name` or `notes` value that starts with `=`, `+`,
`-` or `@` gets a leading `'`, so a spreadsheet never runs it as a formula.

## Requirements

- Python 3.10 or newer
- Statements from **Hello bank! or BNP Paribas Fortis (Belgium), in Dutch**.
  Other banks, and French or English statements, use a different layout
  and won't parse.

## Setup

```bash
git clone <this repo>
cd BNP-pipeline
python -m venv .venv
```

Then install the dependencies:

- Windows: `.venv\Scripts\python -m pip install -r requirements.txt`
- macOS / Linux: `.venv/bin/python -m pip install -r requirements.txt`

## Usage

1. Download your monthly statement PDFs from the bank's app or website and put
   them in `input/<year>/`, for example `input/2026/BE00...-EX-2026-10.pdf`.
   Keep the bank's filename: it must end in `-YYYY-M.pdf` or `-YYYY-MM.pdf`.
2. Run:

   ```bash
   .venv\Scripts\python convert.py        # Windows
   .venv/bin/python convert.py            # macOS / Linux
   ```

   Use `--input` and `--output` to choose other folders.

3. You get one CSV per year in `output/<year>.csv`.

Each run rebuilds every year's CSV from all the PDFs in that year's folder,
sorted by date. Re-running, or adding a month later, never creates duplicates.

## Safety check

For every statement, the previous balance plus all transactions must equal the
current balance. If they don't, the script stops with an error naming the PDF
and writes nothing for that year, so a missed or misread transaction can't
slip through unnoticed.

## Importing into Sure

The column names and order match Sure's transaction import, so it maps them
automatically.

1. *Import* → *Transactions (CSV)*, choose the account, upload `output/<year>.csv`.
2. Configure: date format **YYYY-MM-DD**, number format **1,234.56**, sign
   convention **inflows positive** (expenses are negative in this file).
3. Re-importing the full year's file each month is safe: Sure updates a
   transaction with the same account, date, amount, currency and name instead
   of duplicating it. (If a `name` changes between versions of this tool,
   earlier rows will no longer match.)

## Privacy

- `input/`, `output/`, `*.pdf` and `*.csv` are git-ignored. **Never commit them**:
  the PDFs contain your IBAN and address, and the CSVs still contain the names
  of the people and shops you pay. Git history is permanent, so a file
  committed once stays in it even after you delete it.
- The output is *anonymised for you*, not for the people you pay: counterparty
  names and free-text payment messages are kept on purpose. Read through a CSV
  before you share it.

## Tests

```bash
.venv\Scripts\python -m pytest
```

All test data is invented. The tests that need a real statement run against
the PDFs in your own `input/` folder and are skipped when there are none.

## Contributing

Issues and pull requests are welcome, in particular for statement layouts that
don't parse yet. **Never attach a real statement or a real CSV to an issue.**
Describe the layout, or paste a few lines with every name, number and amount
replaced by invented values.

## License

[MIT](LICENSE)
