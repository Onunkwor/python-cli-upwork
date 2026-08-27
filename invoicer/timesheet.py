"""Read the timesheet CSV and validate every row before anything is billed.

The guiding rule: a row we cannot bill correctly is never billed quietly. It
is collected as an Issue, reported with its spreadsheet row number, and it
stops the run.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from .config import DEFAULT_COLUMNS, REQUIRED_FIELDS, Client, Config
from .errors import TimesheetError
from .parsing import parse_date, parse_decimal, parse_hours

ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class Issue:
    """One problem with one row, phrased so a non-programmer can fix it."""

    row: int | None
    field: str
    value: str
    message: str
    hint: str = ""
    severity: str = ERROR

    @property
    def is_error(self) -> bool:
        return self.severity == ERROR


@dataclass(frozen=True)
class Entry:
    """A validated, billable timesheet row."""

    row: int
    client: Client
    hours: Decimal
    rate: Decimal
    rate_overridden: bool
    work_date: date | None = None
    project: str = ""
    task: str = ""
    description: str = ""
    person: str = ""

    @property
    def amount(self) -> Decimal:
        return self.hours * self.rate


@dataclass
class ReadResult:
    entries: list[Entry]
    issues: list[Issue]
    columns: dict[str, str]
    skipped_rows: int = 0
    total_rows: int = 0

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.is_error]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if not i.is_error]


def _norm_header(name: str) -> str:
    return " ".join((name or "").split()).casefold()


def resolve_columns(header: list[str], config: Config) -> tuple[dict[str, str], list[Issue]]:
    """Map logical fields onto the CSV's real headers.

    Explicit [columns] config wins; anything unmapped is auto-detected from the
    known aliases so a standard export works with no configuration at all.
    """
    issues: list[Issue] = []
    available = {_norm_header(h): h for h in header if h}
    mapping: dict[str, str] = {}

    for field_name, configured in config.columns.items():
        actual = available.get(_norm_header(configured))
        if actual is None:
            issues.append(
                Issue(
                    row=None,
                    field=field_name,
                    value=configured,
                    message=f'[columns] maps "{field_name}" to a column that is not in the CSV.',
                    hint="Columns found: " + ", ".join(h for h in header if h),
                )
            )
        else:
            mapping[field_name] = actual

    for field_name, candidates in DEFAULT_COLUMNS.items():
        if field_name in mapping:
            continue
        for candidate in candidates:
            if candidate in available:
                mapping[field_name] = available[candidate]
                break

    for field_name in REQUIRED_FIELDS:
        if field_name not in mapping:
            issues.append(
                Issue(
                    row=None,
                    field=field_name,
                    value="",
                    message=f'No "{field_name}" column found in the CSV.',
                    hint=(
                        f"Tell the tool which column to use by adding to your config:\n"
                        f'  [columns]\n  {field_name} = "<your column name>"\n'
                        f"Columns found: " + ", ".join(h for h in header if h)
                    ),
                )
            )
    return mapping, issues


def _open_reader(path: Path):
    """Open the CSV, tolerating a UTF-8 BOM and semicolon-delimited exports."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise TimesheetError(f"Timesheet not found: {path}") from exc
    except UnicodeDecodeError:
        try:
            text = path.read_text(encoding="latin-1")
        except OSError as exc:
            raise TimesheetError(f"Could not read {path}: {exc}") from exc
    except OSError as exc:
        raise TimesheetError(f"Could not read {path}: {exc}") from exc

    if not text.strip():
        raise TimesheetError(f"{path} is empty.")

    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","
    return csv.DictReader(text.splitlines(), delimiter=delimiter)


def read_timesheet(path: Path, config: Config, period: str | None = None) -> ReadResult:
    """Read and validate the CSV. Never raises on bad rows -- it collects them."""
    reader = _open_reader(path)
    if not reader.fieldnames:
        raise TimesheetError(f"{path} has no header row.")

    columns, issues = resolve_columns(list(reader.fieldnames), config)
    if any(i.is_error for i in issues):
        return ReadResult(entries=[], issues=issues, columns=columns)

    if period and "date" not in columns:
        issues.append(
            Issue(
                row=None,
                field="date",
                value=period,
                message=f"--period {period} was given but the CSV has no date column.",
                hint='Map one with:\n  [columns]\n  date = "<your date column>"',
            )
        )
        return ReadResult(entries=[], issues=issues, columns=columns)

    def cell(row: dict, field_name: str) -> str:
        column = columns.get(field_name)
        return (row.get(column) or "").strip() if column else ""

    entries: list[Entry] = []
    skipped = 0
    total = 0

    # Row 1 is the header, so data starts at 2 -- the number shown in Excel.
    for row_number, row in enumerate(reader, start=2):
        if not any((v or "").strip() for v in row.values()):
            continue  # blank spacer row
        total += 1

        client_raw = cell(row, "client")
        hours_raw = cell(row, "hours")
        date_raw = cell(row, "date")

        work_date = None
        if "date" in columns:
            work_date = parse_date(date_raw)
            if date_raw and work_date is None:
                issues.append(
                    Issue(
                        row=row_number,
                        field="date",
                        value=date_raw,
                        message="Could not read this date.",
                        hint="Expected a format like 2026-07-14 or 14/07/2026.",
                    )
                )
                continue
            if not date_raw:
                issues.append(
                    Issue(
                        row=row_number,
                        field="date",
                        value="",
                        message="Missing date.",
                        hint="Every row needs a date so it lands in the right billing period.",
                    )
                )
                continue

        # Filter to the requested month before validating rates: a client we do
        # not bill this month should not block this month's run.
        if period and work_date and work_date.strftime("%Y-%m") != period:
            continue

        client = None
        if not client_raw:
            issues.append(
                Issue(
                    row=row_number,
                    field="client",
                    value="",
                    message="Missing client.",
                    hint="Every row must name a client. Fix the row in the CSV, or "
                    "correct it in the time tracker and re-export.",
                )
            )
        else:
            client = config.find_client(client_raw)
            if client is None:
                issues.append(
                    Issue(
                        row=row_number,
                        field="client",
                        value=client_raw,
                        message="This client is not in the config, so it has no rate.",
                        hint=f'Add it to your config:\n  [clients."{client_raw}"]\n'
                        f"  rate = 0.00   # set the real hourly rate\n"
                        f"If it is a misspelling of an existing client, add it as an "
                        f"alias instead.",
                    )
                )

        hours = parse_hours(hours_raw)
        if not hours_raw:
            issues.append(
                Issue(
                    row=row_number,
                    field="hours",
                    value="",
                    message="Missing hours.",
                    hint="Expected a value like 7.5 or 7:30.",
                )
            )
            continue
        if hours is None:
            issues.append(
                Issue(
                    row=row_number,
                    field="hours",
                    value=hours_raw,
                    message="Could not read the hours.",
                    hint="Expected a value like 7.5 or 7:30.",
                )
            )
            continue
        if hours < 0:
            issues.append(
                Issue(
                    row=row_number,
                    field="hours",
                    value=hours_raw,
                    message="Negative hours.",
                    hint="A negative duration is almost always a tracker error. "
                    "Fix it at the source and re-export.",
                )
            )
            continue
        if hours == 0:
            issues.append(
                Issue(
                    row=row_number,
                    field="hours",
                    value=hours_raw,
                    message="Zero hours -- row skipped, nothing billed for it.",
                    severity=WARNING,
                )
            )
            skipped += 1
            continue

        rate = None
        rate_overridden = False
        rate_raw = cell(row, "rate")
        if rate_raw:
            rate = parse_decimal(rate_raw)
            if rate is None:
                issues.append(
                    Issue(
                        row=row_number,
                        field="rate",
                        value=rate_raw,
                        message="Could not read the rate in this row.",
                        hint="Expected a number like 120 or 120.00. Leave it blank "
                        "to use the client's configured rate.",
                    )
                )
                continue
            if rate < 0:
                issues.append(
                    Issue(
                        row=row_number,
                        field="rate",
                        value=rate_raw,
                        message="Negative rate.",
                        hint="Rates must be zero or positive.",
                    )
                )
                continue
            rate_overridden = True
        elif client is not None:
            if client.rate is None:
                issues.append(
                    Issue(
                        row=row_number,
                        field="rate",
                        value=client.key,
                        message=f'No rate configured for client "{client.key}".',
                        hint=f'Set one in your config:\n  [clients."{client.key}"]\n'
                        f"  rate = 0.00   # set the real hourly rate",
                    )
                )
                continue
            rate = client.rate

        if client is None or rate is None:
            continue  # already reported above

        entries.append(
            Entry(
                row=row_number,
                client=client,
                hours=hours,
                rate=rate,
                rate_overridden=rate_overridden,
                work_date=work_date,
                project=cell(row, "project"),
                task=cell(row, "task"),
                description=cell(row, "description"),
                person=cell(row, "person"),
            )
        )

    return ReadResult(
        entries=entries,
        issues=issues,
        columns=columns,
        skipped_rows=skipped,
        total_rows=total,
    )
