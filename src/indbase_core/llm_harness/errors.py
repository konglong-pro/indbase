"""Harness errors."""


class HarnessError(RuntimeError):
    """Base harness failure."""


class HarnessValidationError(HarnessError):
    """Schema or evidence validation failed."""


class HarnessTimeoutError(HarnessError):
    """Provider timeout."""
