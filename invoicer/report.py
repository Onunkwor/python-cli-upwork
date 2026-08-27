"""Terminal reporting. Problems are grouped so a 500-row CSV with 40 bad rows
prints one clear block, not 40 near-identical lines."""

from __future__ import annotations

import os
import sys

from .timesheet import Issue

_ANSI = {
    "reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m",
    "red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m", "cyan": "\033[36m",
}


class Style:
    def __init__(self, enabled: bool | None = None):
        if enabled is None:
            enabled = (
                sys.stdout.isatty()
                and os.environ.get("NO_COLOR") is None
                and os.environ.get("TERM") != "dumb"
            )
        self.enabled = enabled

    def __call__(self, text: str, *names: str) -> str:
        if not self.enabled or not names:
            return text
        return "".join(_ANSI[n] for n in names) + text + _ANSI["reset"]


def _rows_phrase(rows: list[int], limit: int = 12) -> str:
    shown = [str(r) for r in rows[:limit]]
    if len(rows) > limit:
        shown.append(f"and {len(rows) - limit} more")
    return ", ".join(shown)


def group_issues(issues: list[Issue]) -> list[tuple[Issue, list[int], list[str]]]:
    """Collapse identical problems into one entry with the list of rows hit."""
    grouped: dict[tuple, tuple[Issue, list[int], list[str]]] = {}
    for issue in issues:
        key = (issue.severity, issue.field, issue.message, issue.hint)
        if key not in grouped:
            grouped[key] = (issue, [], [])
        _, rows, values = grouped[key]
        if issue.row is not None:
            rows.append(issue.row)
        if issue.value and issue.value not in values:
            values.append(issue.value)
    return list(grouped.values())


def print_issues(issues: list[Issue], style: Style, stream=None) -> None:
    """Print grouped issues with row numbers and a fix hint for each."""
    out = stream or sys.stdout
    for issue, rows, values in group_issues(issues):
        is_error = issue.is_error
        marker = style("x", "red", "bold") if is_error else style("!", "yellow", "bold")
        label = style(issue.field.upper(), "bold")

        if rows:
            where = f"{'Rows' if len(rows) > 1 else 'Row'} {_rows_phrase(rows)}"
        else:
            where = "Header"
        print(f"  {marker} {label}  {style(where, 'dim')}", file=out)
        print(f"    {issue.message}", file=out)

        if values:
            sample = ", ".join(f'"{v}"' for v in values[:5])
            if len(values) > 5:
                sample += f", and {len(values) - 5} more"
            print(f"    {style('Value:', 'dim')} {sample}", file=out)

        if issue.hint:
            for i, line in enumerate(issue.hint.splitlines()):
                prefix = style("-> ", "cyan") if i == 0 else "   "
                print(f"    {prefix}{line}", file=out)
        print(file=out)
