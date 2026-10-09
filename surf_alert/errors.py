"""Exception classes for Surf Alert, plus the secret-redaction helper used in error messages."""

from __future__ import annotations

from urllib.parse import quote_plus


class SurfAlertError(Exception):
    """Base class for every error the app raises on purpose."""


class ConfigError(SurfAlertError):
    """The config file is missing, unreadable, not valid TOML, or has invalid content."""


class SurflineError(SurfAlertError):
    """Fetching or parsing one spot's Surfline forecast failed."""


class JevError(SurfAlertError):
    """The Jev request failed, or Jev's answers could not be used."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code: int | None = status_code


class NotificationError(SurfAlertError):
    """Sending the WhatsApp message failed."""


def redact(text: str, secret: str) -> str:
    """Replace every occurrence of `secret` (and of its URL-encoded form) in `text` with `***`.

    The URL-encoded form is replaced first because, when it differs from the secret,
    it is the longer of the two.
    """
    if not secret:
        return text
    encoded = quote_plus(secret)
    if encoded != secret:
        text = text.replace(encoded, "***")
    return text.replace(secret, "***")
