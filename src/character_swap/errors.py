"""The base of every error the CLI shows as a plain message (exit code 2)."""


class CharSwapError(Exception):
    """Something the user can fix; the message says what to do."""
