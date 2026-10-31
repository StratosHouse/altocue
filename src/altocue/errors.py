"""Errors that reach the user carry a fix they can act on."""


class AltocueError(Exception):
    """A failure the user can do something about."""

    def __init__(self, message: str, fix: str | None = None):
        super().__init__(message)
        self.fix = fix

    def __str__(self) -> str:
        base = super().__str__()
        return f"{base}\nFix: {self.fix}" if self.fix else base
