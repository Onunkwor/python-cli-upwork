"""Command line interface.

    invoicer check     <csv>   validate the CSV, write nothing
    invoicer generate  <csv>   write one PDF invoice per client
    invoicer init-config       scaffold a config from an existing CSV
"""

from __future__ import annotations

import argparse
import csv as csvmod
import re
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from . import __version__
from .config import DEFAULT_COLUMNS, Config, load_config
from .errors import ConfigError, InvoicerError, TimesheetError
from .invoice import build_invoices
from .parsing import format_hours, parse_date
from .pdf import render_invoice
from .report import Style, print_issues
from .timesheet import read_timesheet, resolve_columns

EXIT_OK = 0
EXIT_VALIDATION = 1
EXIT_USAGE = 2


def _safe_filename(text: str) -> str:
    """Client names become filenames, so strip anything a filesystem dislikes."""
    cleaned = re.sub(r"[^\w\s.-]", "", text, flags=re.UNICODE).strip()
    cleaned = re.sub(r"\s+", "-", cleaned)
    return cleaned.strip(".-") or "client"


def _parse_period(value: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}", value):
        raise argparse.ArgumentTypeError(
            f"--period must look like 2026-07, got {value!r}"
        )
    return value


def _parse_issue_date(value: str) -> date:
    parsed = parse_date(value)
    if parsed is None:
        raise argparse.ArgumentTypeError(
            f"--issue-date must look like 2026-07-31, got {value!r}"
        )
    return parsed


def _money_str(symbol: str, value: Decimal) -> str:
    return f"{symbol}{value:,.2f}"


def _load(args) -> Config:
    return load_config(Path(args.config))


def cmd_check(args, style: Style) -> int:
    config = _load(args)
    csv_path = Path(args.csv)
    result = read_timesheet(csv_path, config, period=args.period)

    print(f"\n{style('Timesheet', 'bold')}  {csv_path}")
    print(f"{style('Config', 'bold')}     {config.source}")
    mapped = ", ".join(f"{k} -> {v}" for k, v in sorted(result.columns.items()))
    print(f"{style('Columns', 'bold')}    {mapped or '(none detected)'}\n")

    if result.issues:
        print_issues(result.issues, style)

    billable = sum((e.hours for e in result.entries), Decimal("0"))
    clients = {e.client.key for e in result.entries}
    errors, warnings = result.errors, result.warnings

    print(
        f"{style('Read', 'dim')} {result.total_rows} rows  "
        f"{style('/', 'dim')} {len(result.entries)} billable  "
        f"{style('/', 'dim')} {format_hours(billable)} hours  "
        f"{style('/', 'dim')} {len(clients)} clients"
    )
    if errors:
        print(style(f"\nFAILED: {len(errors)} problem(s) must be fixed.", "red", "bold"))
        if warnings:
            print(style(f"        {len(warnings)} warning(s).", "yellow"))
        return EXIT_VALIDATION
    if warnings:
        print(style(f"\nOK with {len(warnings)} warning(s).", "yellow", "bold"))
        return EXIT_OK
    print(style("\nOK. Every row is billable.", "green", "bold"))
    return EXIT_OK


def cmd_generate(args, style: Style) -> int:
    config = _load(args)
    csv_path = Path(args.csv)
    out_dir = Path(args.out)
    result = read_timesheet(csv_path, config, period=args.period)

    print(f"\n{style('Timesheet', 'bold')}  {csv_path}")
    print(f"{style('Config', 'bold')}     {config.source}\n")

    if result.issues:
        print_issues(result.issues, style)

    errors = result.errors
    if errors and not args.allow_partial:
        print(
            style(
                f"STOPPED: {len(errors)} problem(s) in the timesheet. "
                f"No invoices were written.",
                "red",
                "bold",
            )
        )
        print(
            style(
                "Fix the rows above and run again, or use --allow-partial to invoice "
                "only the rows that are correct.",
                "dim",
            )
        )
        return EXIT_VALIDATION

    if errors and args.allow_partial:
        excluded = len({i.row for i in errors if i.row is not None})
        print(
            style(
                f"WARNING: --allow-partial is on. {excluded} row(s) with problems were "
                f"left off the invoices below.",
                "yellow",
                "bold",
            )
        )
        print()

    if not result.entries:
        scope = f" for period {args.period}" if args.period else ""
        print(style(f"Nothing to invoice{scope}. No billable rows found.", "yellow", "bold"))
        return EXIT_OK

    invoices = build_invoices(
        result.entries,
        config,
        issue_date=args.issue_date,
        start_number=args.start_number,
    )

    period_folder = args.period or (
        min(i.period_start for i in invoices if i.period_start).strftime("%Y-%m")
        if any(i.period_start for i in invoices)
        else datetime.today().strftime("%Y-%m")
    )
    target_dir = out_dir if args.flat else out_dir / period_folder

    symbol = config.invoice.currency_symbol
    rows = []
    grand_total = Decimal("0")
    grand_hours = Decimal("0")

    for invoice in invoices:
        filename = f"{_safe_filename(invoice.client.key)}-{_safe_filename(invoice.number)}.pdf"
        path = target_dir / filename
        if not args.dry_run:
            if path.exists() and not args.overwrite:
                print(
                    style(
                        f"STOPPED: {path} already exists. "
                        f"Re-run with --overwrite to replace it.",
                        "red",
                        "bold",
                    )
                )
                return EXIT_VALIDATION
            render_invoice(invoice, config, path, page_size=args.page_size)
        rows.append((invoice, path))
        grand_total += invoice.total
        grand_hours += invoice.total_hours

    width = max(len(i.client.key) for i, _ in rows)
    width = max(width, len("CLIENT"))
    header = (
        f"  {style('CLIENT'.ljust(width), 'bold')}  {style('HOURS'.rjust(8), 'bold')}  "
        f"{style('TOTAL'.rjust(12), 'bold')}   {style('FILE', 'bold')}"
    )
    verb = "Would write" if args.dry_run else "Wrote"
    print(f"{style(f'{verb} {len(rows)} invoice(s)', 'green', 'bold')} -> {target_dir}\n")
    print(header)
    for invoice, path in rows:
        print(
            f"  {invoice.client.key.ljust(width)}  "
            f"{format_hours(invoice.total_hours).rjust(8)}  "
            f"{_money_str(symbol, invoice.total).rjust(12)}   "
            f"{style(path.name, 'dim')}"
        )
    print(
        f"\n  {'TOTAL'.ljust(width)}  {format_hours(grand_hours).rjust(8)}  "
        f"{style(_money_str(symbol, grand_total).rjust(12), 'bold')}"
    )

    if result.warnings:
        print(style(f"\n  {len(result.warnings)} warning(s) above -- worth a look.", "yellow"))

    if args.summary and not args.dry_run:
        _write_summary(Path(args.summary), rows, config)
        print(f"\n  Summary written to {args.summary}")

    if args.dry_run:
        print(style("\n  Dry run: no files were written.", "dim"))
    return EXIT_OK


def _write_summary(path: Path, rows, config: Config) -> None:
    """A one-line-per-invoice CSV, for pasting into the accounts spreadsheet."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csvmod.writer(handle)
        writer.writerow(
            ["invoice_number", "client", "period", "issue_date", "due_date",
             "hours", "subtotal", "tax", "total", "currency", "file"]
        )
        for invoice, pdf_path in rows:
            writer.writerow([
                invoice.number,
                invoice.client.key,
                invoice.period_label,
                invoice.issue_date.isoformat(),
                invoice.due_date.isoformat(),
                format_hours(invoice.total_hours, config.invoice.hours_decimals),
                f"{invoice.subtotal:.2f}",
                f"{invoice.tax_amount:.2f}",
                f"{invoice.total:.2f}",
                invoice.currency,
                str(pdf_path),
            ])


def cmd_init_config(args, style: Style) -> int:
    """Scaffold a config file, pre-filled from a real CSV where possible."""
    out_path = Path(args.out)
    if out_path.exists() and not args.overwrite:
        raise ConfigError(f"{out_path} already exists. Use --overwrite to replace it.")

    columns: dict[str, str] = {}
    clients: list[str] = []

    if args.from_csv:
        csv_path = Path(args.from_csv)
        blank = Config()
        try:
            text = csv_path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            raise TimesheetError(f"Could not read {csv_path}: {exc}") from exc
        reader = csvmod.DictReader(text.splitlines())
        if not reader.fieldnames:
            raise TimesheetError(f"{csv_path} has no header row.")
        columns, _ = resolve_columns(list(reader.fieldnames), blank)
        client_col = columns.get("client")
        if client_col:
            seen = {}
            for row in reader:
                name = (row.get(client_col) or "").strip()
                if name:
                    seen.setdefault(name.casefold(), name)
            clients = sorted(seen.values(), key=str.casefold)

    lines = [
        "# invoicer configuration",
        "# Edit the rates below, then run:  invoicer check <your.csv>",
        "",
        "[company]",
        'name = "Your Company Ltd"',
        'address_lines = ["1 Example Street", "London", "EC1A 1AA"]',
        'email = "accounts@example.com"',
        'phone = "+44 20 7946 0000"',
        'tax_id = "VAT GB123456789"',
        '# logo = "logo.png"',
        "",
        "[invoice]",
        'prefix = "INV"',
        'number_format = "{prefix}-{period}-{seq:03d}"',
        "payment_terms_days = 14",
        'currency = "USD"',
        'currency_symbol = "$"',
        'notes = "Thank you for your business."',
        '# One invoice line per unique combination of these fields:',
        'group_by = ["project", "description"]',
        "",
    ]

    if columns:
        lines.append("# Detected from your CSV. Change these if the tool guessed wrong.")
        lines.append("[columns]")
        for field_name in sorted(DEFAULT_COLUMNS):
            actual = columns.get(field_name)
            if actual:
                lines.append(f'{field_name} = "{actual}"')
            else:
                lines.append(f'# {field_name} = "<column name>"')
        lines.append("")

    if clients:
        lines.append(f"# {len(clients)} client(s) found in the CSV. Set each real rate.")
        for name in clients:
            escaped = name.replace('"', '\\"')
            lines += [
                f'[clients."{escaped}"]',
                "rate = 0.00   # TODO set the hourly rate",
                f'bill_to = ["{escaped}", "Client address line 1", "City, Postcode"]',
                '# email = "ap@client.example"',
                "# tax_rate = 0.20",
                '# aliases = ["Alternate spelling in the tracker"]',
                "",
            ]
    else:
        lines += [
            '[clients."Example Client"]',
            "rate = 120.00",
            'bill_to = ["Example Client Ltd", "2 Client Road", "Manchester, M1 2AB"]',
            "",
        ]

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n{style('Wrote', 'green', 'bold')} {out_path}")
    if columns:
        print(f"  Detected columns: {', '.join(f'{k} -> {v}' for k, v in sorted(columns.items()))}")
    if clients:
        print(f"  Found {len(clients)} client(s): {', '.join(clients[:6])}"
              + (" ..." if len(clients) > 6 else ""))
        print(style("  Every rate is 0.00 -- set the real rates before invoicing.", "yellow"))
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="invoicer",
        description="Turn a time-tracker CSV export into one PDF invoice per client.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  invoicer check hours.csv\n"
            "  invoicer generate hours.csv --period 2026-07 --out invoices\n"
            "  invoicer generate hours.csv --dry-run\n"
            "  invoicer init-config --from-csv hours.csv\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"invoicer {__version__}")
    parser.add_argument("--no-color", action="store_true", help="disable coloured output")

    # Repeated on every subcommand so `invoicer check x.csv --no-color` works too.
    # SUPPRESS keeps the subparser default from clobbering a top-level --no-color.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS,
                        help="disable coloured output")

    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p):
        p.add_argument("csv", help="the timesheet CSV exported from your time tracker")
        p.add_argument("-c", "--config", default="clients.toml",
                       help="config file with rates (default: clients.toml)")
        p.add_argument("--period", type=_parse_period, metavar="YYYY-MM",
                       help="only bill rows in this month, e.g. 2026-07")

    check = sub.add_parser("check", parents=[common],
                           help="validate the CSV without writing anything")
    add_common(check)
    check.set_defaults(func=cmd_check)

    gen = sub.add_parser("generate", parents=[common],
                         help="write one PDF invoice per client")
    add_common(gen)
    gen.add_argument("-o", "--out", default="invoices",
                     help="output folder (default: invoices)")
    gen.add_argument("--issue-date", type=_parse_issue_date, default=None,
                     metavar="YYYY-MM-DD", help="invoice date (default: today)")
    gen.add_argument("--start-number", type=int, default=1,
                     help="first sequence number for this run (default: 1)")
    gen.add_argument("--page-size", default="A4", choices=["A4", "LETTER", "a4", "letter"],
                     help="page size (default: A4)")
    gen.add_argument("--allow-partial", action="store_true",
                     help="invoice the valid rows and leave out the broken ones")
    gen.add_argument("--dry-run", action="store_true",
                     help="show what would be produced, write nothing")
    gen.add_argument("--overwrite", action="store_true",
                     help="replace invoice files that already exist")
    gen.add_argument("--flat", action="store_true",
                     help="write PDFs straight into --out, with no month subfolder")
    gen.add_argument("--summary", metavar="PATH",
                     help="also write a one-line-per-invoice CSV summary")
    gen.set_defaults(func=cmd_generate)

    init = sub.add_parser("init-config", parents=[common],
                          help="scaffold a config file")
    init.add_argument("--from-csv", metavar="PATH",
                      help="read column names and client names from this CSV")
    init.add_argument("-o", "--out", default="clients.toml",
                      help="where to write the config (default: clients.toml)")
    init.add_argument("--overwrite", action="store_true", help="replace an existing config")
    init.set_defaults(func=cmd_init_config)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    style = Style(enabled=False if args.no_color else None)
    try:
        return args.func(args, style)
    except (ConfigError, TimesheetError) as exc:
        print(style(f"\nError: {exc}", "red", "bold"), file=sys.stderr)
        return EXIT_USAGE
    except InvoicerError as exc:
        print(style(f"\nError: {exc}", "red", "bold"), file=sys.stderr)
        return EXIT_VALIDATION
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
