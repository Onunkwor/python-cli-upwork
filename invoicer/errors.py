"""Exception types. Every failure path in the tool raises one of these."""


class InvoicerError(Exception):
    """Base class for errors we expect and report cleanly (no traceback)."""


class ConfigError(InvoicerError):
    """The config file is missing, unreadable, or malformed."""


class TimesheetError(InvoicerError):
    """The CSV is missing, unreadable, or has no usable header."""


class ValidationFailed(InvoicerError):
    """The CSV was read but contains rows we refuse to invoice."""

    def __init__(self, issues):
        self.issues = issues
        super().__init__(f"{len(issues)} problem(s) found in the timesheet")
