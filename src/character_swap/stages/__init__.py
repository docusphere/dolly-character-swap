"""Pipeline steps. Each module does one step and receives its provider client."""


class StageError(RuntimeError):
    """A pipeline step could not complete; the message says what to do."""
