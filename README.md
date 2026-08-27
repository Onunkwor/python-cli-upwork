# invoicer

Turn a monthly time-tracker CSV export into one PDF invoice per client.

Runs entirely on your own machine. No server, no account, no upload. One command
replaces the day someone currently spends copying hours into Word.

```
$ invoicer generate hours.csv --period 2026-07 --out invoices

Wrote 4 invoice(s) -> invoices/2026-07

  CLIENT                HOURS         TOTAL   FILE
  Acme Corp             43.75     £4,987.50   Acme-Corp-INV-2026-07-001.pdf
  Globex Industries     25.75     £3,708.00   Globex-Industries-INV-2026-07-002.pdf
  Initech LLC           32.25     £3,547.50   Initech-LLC-INV-2026-07-003.pdf
  Umbrella Health        51.5     £8,961.00   Umbrella-Health-INV-2026-07-004.pdf

  TOTAL                153.25    £21,204.00
```

## It refuses to guess

A wrong invoice is worse than no invoice. If any row is missing a client, names a
client with no configured rate, or has hours that cannot be read, the run stops
and **nothing is written**:

```
$ invoicer generate hours.csv

  x CLIENT  Rows 5, 25
    Missing client.
    -> Every row must name a client. Fix the row in the CSV, or correct it in the
       time tracker and re-export.

  x CLIENT  Row 9
    This client is not in the config, so it has no rate.
    Value: "Soylent Corp"
    -> Add it to your config:
         [clients."Soylent Corp"]
         rate = 0.00   # set the real hourly rate
       If it is a misspelling of an existing client, add it as an alias instead.

  x HOURS  Row 14
    Could not read the hours.
    Value: "half a day"
    -> Expected a value like 7.5 or 7:30.

STOPPED: 7 problem(s) in the timesheet. No invoices were written.
```

Row numbers are the ones you see in Excel, so you can go straight to the cell.
Identical problems are grouped into one block rather than repeated per row.
The exit code is non-zero, so this drops straight into a script if you ever want it to.

## Install

Requires Python 3.11 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .
```

That puts an `invoicer` command on your PATH. You can also run it without
installing, as `python -m invoicer`.

## Getting started

**1. Scaffold a config from your real CSV.** This reads your export, works out
which column is which, and lists every client it finds:

```bash
invoicer init-config --from-csv hours.csv
```

**2. Fill in the rates.** Open `clients.toml` and replace each `rate = 0.00`.

**3. Check before you commit to anything.** Writes no files:

```bash
invoicer check hours.csv
```

**4. Generate.**

```bash
invoicer generate hours.csv --period 2026-07 --out invoices
```

## Commands

| Command | What it does |
| --- | --- |
| `invoicer check <csv>` | Validate the CSV and report problems. Writes nothing. Exit 1 if there are errors. |
| `invoicer generate <csv>` | Write one PDF per client. Stops without writing if the CSV has problems. |
| `invoicer init-config` | Scaffold a config file, pre-filled from an existing CSV. |

Useful flags on `generate`:

| Flag | Effect |
| --- | --- |
| `--period 2026-07` | Only bill rows dated in that month. |
| `--out FOLDER` | Where PDFs go (default `invoices/`, in a `YYYY-MM` subfolder). |
| `--flat` | Skip the month subfolder. |
| `--issue-date 2026-07-31` | Invoice date, and the basis for the due date. Defaults to today. |
| `--start-number 42` | First sequence number of the run, to continue your existing numbering. |
| `--dry-run` | Show exactly what would be produced, write nothing. |
| `--summary out.csv` | Also write one line per invoice, for the accounts spreadsheet. |
| `--overwrite` | Replace PDFs that already exist. Without it, an existing file stops the run. |
| `--allow-partial` | Invoice the good rows and leave the broken ones off. Off by default, on purpose. |
| `--page-size LETTER` | US Letter instead of A4. |

## Configuration

Everything lives in one `clients.toml`. Nothing is hardcoded.

```toml
[company]
name = "Northgate Studio Ltd"
address_lines = ["14 Cargill Road", "Bristol", "BS1 4TR"]
email = "accounts@northgatestudio.com"
tax_id = "VAT GB428 9917 03"
# logo = "logo.png"                 # optional, appears top-left

[invoice]
prefix = "INV"
number_format = "{prefix}-{period}-{seq:03d}"   # -> INV-2026-07-001
payment_terms_days = 14
currency = "GBP"
currency_symbol = "£"
notes = "Please quote the invoice number with your payment."
group_by = ["project", "task"]      # one invoice line per unique combination

[columns]                           # only needed if auto-detection guesses wrong
date = "Date"
client = "Client"
hours = "Duration (h)"

[clients."Acme Corp"]
rate = 95.00
bill_to = ["Acme Corp", "Attn: Accounts Payable", "1 Sherwood Park", "Nottingham"]
email = "ap@acme.example"
tax_rate = 0.20
tax_label = "VAT"
aliases = ["Acme", "ACME Corp.", "Acme Corporation"]
payment_terms_days = 30             # optional, overrides the default
```

**Column mapping.** Every time tracker names its columns differently. Standard
exports are detected automatically (`Hours`, `Duration`, `Duration (h)`, `Time`,
and so on); when yours is unusual, name it in `[columns]` rather than editing code.

**Aliases.** Trackers accumulate spelling variants of the same client. Listing
them as aliases folds them onto one invoice instead of producing two.

**Grouping.** `group_by` controls how many lines appear on the invoice. Four
hundred time entries usually want to be a handful of lines, grouped by project
and task, not four hundred rows.

## What the invoice looks like

Header with your company details and logo, invoice number, issue and due dates,
billing period, a Bill To block, the line-item table, subtotal, optional tax, and
Total Due, then payment terms and notes. Long invoices continue across pages with
the table header repeated and `Page 2 of 17` in the footer.

Sample output is in `invoices/2026-07/`, generated from `samples/`.

## Notes on correctness

- **Money is `Decimal`, never `float`.** `0.1 + 0.2` is not `0.3` in binary
  floating point, and an invoice a cent out is a phone call.
- **Rounding is half-up**, the way a spreadsheet rounds, not Python's default
  banker's rounding.
- **Rounding happens once per invoice line**, not per time entry, so a hundred
  short entries do not accumulate drift.
- **Lines at different rates never merge**, even if they group to the same
  description. A single line cannot show one rate and a total from another.
- **Existing files are never silently replaced.** Re-running is safe.

Hours parse from `7.5`, `7:30` or `7h 30m`. Dates parse from ISO, `14/07/2026`,
`07/14/2026` and several others. Semicolon-delimited exports and a UTF-8 BOM are
handled. Blank spacer rows are skipped.

## Tests

```bash
pip install pytest
pytest -q
```

69 tests covering parsing, validation, grouping, totals, tax, PDF output and
every CLI exit code.

## Try it on the samples

```bash
invoicer check    samples/timesheet-2026-07.csv -c samples/clients.toml
invoicer generate samples/timesheet-2026-07.csv -c samples/clients.toml \
         --period 2026-07 --issue-date 2026-07-31 --out invoices \
         --summary invoices/summary-2026-07.csv

# and the deliberately broken one, to see the validation
invoicer check    samples/timesheet-messy.csv -c samples/clients.toml
```

## License

MIT — see [LICENSE](LICENSE).
