"""The behaviour the client cared about most: never invoice a bad row quietly."""

from decimal import Decimal
from pathlib import Path

import pytest

from invoicer.config import load_config
from invoicer.timesheet import read_timesheet

CONFIG = """
[company]
name = "Test Co"

[invoice]
currency_symbol = "$"

[clients."Acme Corp"]
rate = 100.00
aliases = ["ACME", "Acme Corp."]

[clients."No Rate Ltd"]
bill_to = ["No Rate Ltd"]
"""

HEADER = "Date,Client,Project,Description,Duration (h)\n"


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "clients.toml"
    path.write_text(CONFIG)
    return load_config(path)


def write_csv(tmp_path, body, name="hours.csv"):
    path = tmp_path / name
    path.write_text(HEADER + body)
    return path


def issues_for(result, field):
    return [i for i in result.issues if i.field == field]


def test_clean_rows_are_billable(tmp_path, config):
    csv = write_csv(tmp_path, "2026-07-01,Acme Corp,Site,Homepage,3\n")
    result = read_timesheet(csv, config)
    assert result.errors == []
    assert len(result.entries) == 1
    assert result.entries[0].rate == Decimal("100.00")


def test_missing_client_is_an_error_and_is_not_billed(tmp_path, config):
    csv = write_csv(tmp_path, "2026-07-01,,Site,Homepage,3\n")
    result = read_timesheet(csv, config)
    assert result.entries == []
    assert issues_for(result, "client")[0].message == "Missing client."
    assert result.errors[0].row == 2  # the spreadsheet row number, not the index


def test_unknown_client_is_an_error_with_a_copy_pasteable_fix(tmp_path, config):
    csv = write_csv(tmp_path, "2026-07-01,Soylent Corp,Site,Homepage,3\n")
    result = read_timesheet(csv, config)
    assert result.entries == []
    issue = issues_for(result, "client")[0]
    assert "not in the config" in issue.message
    assert '[clients."Soylent Corp"]' in issue.hint


def test_client_without_a_configured_rate_is_an_error(tmp_path, config):
    csv = write_csv(tmp_path, "2026-07-01,No Rate Ltd,Site,Homepage,3\n")
    result = read_timesheet(csv, config)
    assert result.entries == []
    assert "No rate configured" in issues_for(result, "rate")[0].message


@pytest.mark.parametrize("hours,fragment", [("", "Missing hours"), ("abc", "Could not read")])
def test_bad_hours_are_errors(tmp_path, config, hours, fragment):
    csv = write_csv(tmp_path, f"2026-07-01,Acme Corp,Site,Homepage,{hours}\n")
    result = read_timesheet(csv, config)
    assert result.entries == []
    assert fragment in issues_for(result, "hours")[0].message


def test_negative_hours_are_an_error(tmp_path, config):
    csv = write_csv(tmp_path, "2026-07-01,Acme Corp,Site,Homepage,-2\n")
    result = read_timesheet(csv, config)
    assert result.entries == []
    assert result.errors


def test_zero_hours_is_a_warning_not_an_error(tmp_path, config):
    csv = write_csv(tmp_path, "2026-07-01,Acme Corp,Site,Homepage,0\n")
    result = read_timesheet(csv, config)
    assert result.errors == []
    assert len(result.warnings) == 1
    assert result.skipped_rows == 1


def test_missing_date_is_an_error(tmp_path, config):
    csv = write_csv(tmp_path, ",Acme Corp,Site,Homepage,3\n")
    result = read_timesheet(csv, config)
    assert result.entries == []
    assert issues_for(result, "date")


def test_aliases_resolve_to_the_canonical_client(tmp_path, config):
    csv = write_csv(
        tmp_path,
        "2026-07-01,ACME,Site,A,1\n2026-07-02,Acme Corp.,Site,B,1\n2026-07-03,acme corp,Site,C,1\n",
    )
    result = read_timesheet(csv, config)
    assert result.errors == []
    assert {e.client.key for e in result.entries} == {"Acme Corp"}


def test_every_bad_row_is_reported_not_just_the_first(tmp_path, config):
    csv = write_csv(
        tmp_path,
        "2026-07-01,,Site,A,1\n2026-07-02,Acme Corp,Site,B,abc\n2026-07-03,Nobody,Site,C,1\n",
    )
    result = read_timesheet(csv, config)
    assert len(result.errors) == 3
    assert [i.row for i in result.errors] == [2, 3, 4]


def test_blank_spacer_rows_are_ignored(tmp_path, config):
    csv = write_csv(tmp_path, "2026-07-01,Acme Corp,Site,A,1\n,,,,\n")
    result = read_timesheet(csv, config)
    assert result.errors == []
    assert len(result.entries) == 1


def test_period_filter_excludes_other_months(tmp_path, config):
    csv = write_csv(tmp_path, "2026-06-30,Acme Corp,Site,A,1\n2026-07-01,Acme Corp,Site,B,2\n")
    result = read_timesheet(csv, config, period="2026-07")
    assert len(result.entries) == 1
    assert result.entries[0].hours == Decimal("2")


def test_period_filter_ignores_bad_clients_in_other_months(tmp_path, config):
    """A client we do not bill this month must not block this month's run."""
    csv = write_csv(tmp_path, "2026-06-30,Soylent Corp,Site,A,1\n2026-07-01,Acme Corp,Site,B,2\n")
    result = read_timesheet(csv, config, period="2026-07")
    assert result.errors == []
    assert len(result.entries) == 1


def test_missing_required_column_is_reported_with_the_available_columns(tmp_path, config):
    path = tmp_path / "bad.csv"
    path.write_text("Date,Project,Notes\n2026-07-01,Site,Homepage\n")
    result = read_timesheet(path, config)
    messages = " ".join(i.message for i in result.errors)
    assert "client" in messages and "hours" in messages
    assert "Columns found" in " ".join(i.hint for i in result.errors)


def test_semicolon_delimited_export_is_read(tmp_path, config):
    path = tmp_path / "semi.csv"
    path.write_text("Date;Client;Project;Description;Duration (h)\n2026-07-01;Acme Corp;Site;A;3\n")
    result = read_timesheet(path, config)
    assert result.errors == []
    assert len(result.entries) == 1


def test_utf8_bom_is_stripped(tmp_path, config):
    path = tmp_path / "bom.csv"
    path.write_bytes(b"\xef\xbb\xbf" + (HEADER + "2026-07-01,Acme Corp,Site,A,3\n").encode())
    result = read_timesheet(path, config)
    assert result.errors == []
    assert len(result.entries) == 1


def test_row_level_rate_overrides_the_configured_rate(tmp_path, config):
    path = tmp_path / "rate.csv"
    path.write_text(
        "Date,Client,Project,Description,Duration (h),Rate\n"
        "2026-07-01,Acme Corp,Site,A,2,150\n"
    )
    result = read_timesheet(path, config)
    assert result.errors == []
    assert result.entries[0].rate == Decimal("150")
    assert result.entries[0].rate_overridden is True
