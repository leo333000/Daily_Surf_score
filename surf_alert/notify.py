"""WhatsApp delivery through the CallMeBot free personal API, behind a one-method Notifier."""

from __future__ import annotations

import logging
import re
from typing import Protocol

import requests

from surf_alert.errors import NotificationError, redact

logger = logging.getLogger(__name__)

CALLMEBOT_URL: str = "https://api.callmebot.com/whatsapp.php"
PHONE_PATTERN: str = r"^\+[1-9][0-9]{6,14}$"
TEST_MESSAGE: str = "Surf alert test message: WhatsApp notifications are working."


class Notifier(Protocol):
    def send(self, text: str) -> None: ...


class CallMeBotNotifier:
    def __init__(
        self,
        phone: str,
        api_key: str,
        *,
        session: requests.Session | None = None,
        timeout: float = 30.0,
    ) -> None:
        if not isinstance(phone, str) or re.fullmatch(PHONE_PATTERN, phone) is None:
            raise ValueError("WHATSAPP_PHONE must be in international format, e.g. +34612345678")
        if not isinstance(api_key, str) or api_key.strip() == "":
            raise ValueError("CALLMEBOT_API_KEY must not be empty")
        self._phone = phone
        self._api_key = api_key
        self._session = session if session is not None else requests.Session()
        self._timeout = timeout

    def _redact(self, text: str) -> str:
        return redact(text, self._api_key)

    def send(self, text: str) -> None:
        if not isinstance(text, str) or text.strip() == "":
            raise ValueError("message text must not be empty")

        logger.debug("sending WhatsApp message (%d characters) through %s", len(text), CALLMEBOT_URL)
        try:
            response = self._session.get(
                CALLMEBOT_URL,
                params={"phone": self._phone, "text": text, "apikey": self._api_key},
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise NotificationError(f"request to CallMeBot failed: {self._redact(str(exc))}") from None

        code = response.status_code
        logger.debug("CallMeBot responded with HTTP %s", code)
        if code != 200:
            body = self._redact(response.text or "")[:200]
            raise NotificationError(f"CallMeBot returned HTTP {code}: {body}")
