"""Provider failures safe to show without response bodies or credentials."""


class ProviderError(RuntimeError):
    """A sanitized provider failure that contains no response body."""
