"""Client for TypeSafe's Jev decision model (POST /v1/systemone), called directly with requests."""

from __future__ import annotations

import logging
from typing import Protocol

import requests

from surf_alert.errors import JevError, redact

logger = logging.getLogger(__name__)

JEV_URL: str = "https://api.typesafe.ai/v1/systemone"
DEFAULT_TIMEOUT: float = 30.0


class JevLike(Protocol):
    def ask(self, state: dict | list | str, questions: dict[str, dict]) -> dict[str, dict]: ...


class JevClient:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = "jev-latest",
        session: requests.Session | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        if not isinstance(api_key, str) or api_key.strip() == "":
            raise ValueError("api_key must not be empty")
        self._api_key = api_key
        self._model = model
        self._session = session if session is not None else requests.Session()
        self._timeout = timeout

    def _redact(self, text: str) -> str:
        return redact(text, self._api_key)

    def ask(self, state: dict | list | str, questions: dict[str, dict]) -> dict[str, dict]:
        if not questions:
            raise ValueError("questions must not be empty")

        logger.debug("asking Jev (%s) %d question(s) at %s", self._model, len(questions), JEV_URL)
        try:
            response = self._session.post(
                JEV_URL,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": self._model, "state": state, "questions": questions},
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise JevError(f"request failed: {self._redact(str(exc))}") from None

        code = response.status_code
        logger.debug("Jev responded with HTTP %s", code)
        if not 200 <= code <= 299:
            body = self._redact(response.text or "")[:200]
            if code == 401:
                message = "authentication failed (HTTP 401); check TYPESAFE_API_KEY"
            elif code == 422:
                message = f"invalid request (HTTP 422): {body}"
            elif code == 429:
                message = "rate limited (HTTP 429)"
            elif code == 529:
                message = "service overloaded (HTTP 529)"
            else:
                message = f"unexpected response (HTTP {code}): {body}"
            raise JevError(message, code)

        try:
            data = response.json()
        except ValueError:
            raise JevError("malformed response: body is not valid JSON", code) from None
        if not isinstance(data, dict) or not isinstance(data.get("answers"), dict):
            raise JevError("malformed response: missing 'answers' object", code)
        return data["answers"]
