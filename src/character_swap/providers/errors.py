"""Provider-neutral errors, so stages never depend on one provider's classes."""

from __future__ import annotations

from character_swap.errors import CharSwapError


class ProviderError(CharSwapError, RuntimeError):
    """A provider rejected, failed or has not finished a request."""


class OutputExpired(ProviderError):
    """A finished result's download link is gone; only a new (paid) run recovers it."""
