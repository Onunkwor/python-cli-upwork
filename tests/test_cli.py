"""End-to-end tests through the command line, including exit codes."""

from pathlib import Path

import pytest

from invoicer.cli import main

CONFIG = """
[company]
name = "Test Co"
address_lines = ["1 Test Street", "Testville"]

[invoice]
currency = "USD"
currency_symbol = "$"
payment_terms_days = 14
group_by = ["project", "task"]

[clients."Acme Corp"]
rate = 100.00
bill_to = ["Acme Corp", "1 Acme Way"]
tax_rate = 0.20

[clients."Globex"]
rate = 120.00
"""

CSV = """Date,Client,Project,Task,Duration (h)
2026-07-01,Acme Corp,Site,Design,3
2026-07-02,Acme Corp,Site,Design,2.5
2026-07-03,Globex,Migration,Engineering,4
"""

BAD_CSV = """Date,Client,Project,Task,Duration (h)
2026-07-01,Acme Corp,Site,Design,3
2026-07-02,,Site,Design,2
2026-07-03,Soylent Corp,Site,Design,1
"""


@pytest.fixture
def project(tmp_path):
    (tmp_path / "clients.toml").write_text(CONFIG)
    (tmp_path / "hours.csv").write_text(CSV)
    (tmp_path / "bad.csv").write_text(BAD_CSV)
    return tmp_path


def run(project, *args):
    return main([*args, "--no-color"])


def test_check_passes_on_a_clean_csv(project, capsys):
    code = run(project, "check", str(project / "hours.csv"), "-c", str(project / "clients.toml"))
    assert code == 0
    assert "OK. Every row is billable." in capsys.readouterr().out


def test_check_fails_on_a_bad_csv(project, capsys):
    code = run(project, "check", str(project / "bad.csv"), "-c", str(project / "clients.toml"))
    assert code == 1
    assert "FAILED" in capsys.readouterr().out


def test_generate_writes_one_pdf_per_client(project, capsys):
    out = project / "invoices"
    code = run(project, "generate", str(project / "hours.csv"),
               "-c", str(project / "clients.toml"), "-o", str(out),
               "--issue-date", "2026-07-31")
    assert code == 0
    pdfs = sorted(p.name for p in out.rglob("*.pdf"))
    assert len(pdfs) == 2
    assert pdfs[0].startswith("Acme-Corp-")
    for pdf in out.rglob("*.pdf"):
        assert pdf.read_bytes().startswith(b"%PDF")
        assert pdf.stat().st_size > 1000


def test_generate_writes_nothing_when_the_csv_has_problems(project, capsys):
    """The headline promise: a bad row stops the run, it does not slip through."""
    out = project / "invoices"
    code = run(project, "generate", str(project / "bad.csv"),
               "-c", str(project / "clients.toml"), "-o", str(out))
    assert code == 1
    assert not out.exists() or not list(out.rglob("*.pdf"))
    stdout = capsys.readouterr().out
    assert "No invoices were written" in stdout


def test_allow_partial_bills_the_good_rows_and_says_so(project, capsys):
    out = project / "invoices"
    code = run(project, "generate", str(project / "bad.csv"),
               "-c", str(project / "clients.toml"), "-o", str(out), "--allow-partial")
    assert code == 0
    assert len(list(out.rglob("*.pdf"))) == 1
    assert "--allow-partial is on" in capsys.readouterr().out


def test_dry_run_writes_nothing(project, capsys):
    out = project / "invoices"
    code = run(project, "generate", str(project / "hours.csv"),
               "-c", str(project / "clients.toml"), "-o", str(out), "--dry-run")
    assert code == 0
    assert not out.exists()
    assert "no files were written" in capsys.readouterr().out


def test_existing_files_are_not_overwritten_without_the_flag(project, capsys):
    out = project / "invoices"
    args = ["generate", str(project / "hours.csv"), "-c", str(project / "clients.toml"),
            "-o", str(out), "--issue-date", "2026-07-31"]
    assert run(project, *args) == 0
    assert run(project, *args) == 1
    assert "already exists" in capsys.readouterr().out
    assert run(project, *args, "--overwrite") == 0


def test_summary_csv_matches_the_invoices(project):
    out = project / "invoices"
    summary = project / "summary.csv"
    run(project, "generate", str(project / "hours.csv"), "-c", str(project / "clients.toml"),
        "-o", str(out), "--summary", str(summary), "--issue-date", "2026-07-31")
    lines = summary.read_text().strip().splitlines()
    assert lines[0].startswith("invoice_number,client")
    assert len(lines) == 3
    assert "Acme Corp" in lines[1] and "660.00" in lines[1]  # 5.5h x 100 + 20% VAT


def test_period_filter_limits_the_run(project, capsys):
    (project / "two-months.csv").write_text(
        "Date,Client,Project,Task,Duration (h)\n"
        "2026-06-30,Globex,Migration,Engineering,4\n"
        "2026-07-01,Acme Corp,Site,Design,3\n"
    )
    out = project / "invoices"
    code = run(project, "generate", str(project / "two-months.csv"),
               "-c", str(project / "clients.toml"), "-o", str(out), "--period", "2026-07")
    assert code == 0
    assert len(list(out.rglob("*.pdf"))) == 1


def test_missing_config_is_a_clean_error_not_a_traceback(project, capsys):
    code = run(project, "check", str(project / "hours.csv"), "-c", str(project / "nope.toml"))
    assert code == 2
    assert "Config file not found" in capsys.readouterr().err


def test_missing_csv_is_a_clean_error(project, capsys):
    code = run(project, "check", str(project / "nope.csv"), "-c", str(project / "clients.toml"))
    assert code == 2
    assert "not found" in capsys.readouterr().err


def test_init_config_scaffolds_from_a_csv(project, capsys):
    out = project / "generated.toml"
    code = run(project, "init-config", "--from-csv", str(project / "hours.csv"), "-o", str(out))
    assert code == 0
    text = out.read_text()
    assert '[clients."Acme Corp"]' in text
    assert '[clients."Globex"]' in text
    assert 'hours = "Duration (h)"' in text
    assert "TODO set the hourly rate" in text


def test_generated_config_is_immediately_loadable(project):
    out = project / "generated.toml"
    run(project, "init-config", "--from-csv", str(project / "hours.csv"), "-o", str(out))
    from invoicer.config import load_config
    config = load_config(out)
    assert set(config.clients) == {"Acme Corp", "Globex"}


def test_invalid_period_is_rejected(project):
    with pytest.raises(SystemExit) as exc:
        run(project, "check", str(project / "hours.csv"),
            "-c", str(project / "clients.toml"), "--period", "July")
    assert exc.value.code == 2
