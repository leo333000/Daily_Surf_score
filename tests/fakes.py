"""Test doubles for the surf alert tests (test strategy section 6).

This module only uses the standard library, so it imports even when the
`surf_alert` package is not available.
"""

from __future__ import annotations

import copy
import json as _json

_MISSING = object()


class FakeResponse:
    """Minimal stand-in for `requests.Response`.

    `.text` defaults to `json.dumps(json_data)` when data is given, else "".
    `.json()` raises `json_exc` if set, raises `ValueError("not JSON")` when
    there is no data, and otherwise returns a deep copy of the data.
    """

    def __init__(self, status_code=200, json_data=_MISSING, text=None, json_exc=None):
        self.status_code = status_code
        self._json_data = json_data
        self._json_exc = json_exc
        if text is None:
            text = _json.dumps(json_data) if json_data is not _MISSING else ""
        self.text = text

    def json(self):
        if self._json_exc is not None:
            raise self._json_exc
        if self._json_data is _MISSING:
            raise ValueError("not JSON")
        return copy.deepcopy(self._json_data)


class FakeSession:
    """Records `post`/`get` calls and returns a canned response or raises.

    Each call is recorded in `calls` as a dict:
    - post: {"method": "post", "url", "headers", "json", "timeout"}
    - get:  {"method": "get", "url", "params", "timeout"}
    """

    def __init__(self, response=None, exc=None):
        self.response = response if response is not None else FakeResponse(200, text="ok")
        self.exc = exc
        self.calls = []

    def post(self, url, *, headers, json, timeout):
        self.calls.append(
            {
                "method": "post",
                "url": url,
                "headers": copy.deepcopy(headers),
                "json": copy.deepcopy(json),
                "timeout": timeout,
            }
        )
        return self._respond()

    def get(self, url, *, params, timeout):
        self.calls.append(
            {
                "method": "get",
                "url": url,
                "params": copy.deepcopy(params),
                "timeout": timeout,
            }
        )
        return self._respond()

    def _respond(self):
        if self.exc is not None:
            raise self.exc
        return self.response


class RoutingSession(FakeSession):
    """A `FakeSession` whose `get` answers each URL from `{url: FakeResponse or Exception}`."""

    def __init__(self, responses):
        super().__init__()
        self.responses = dict(responses)

    def get(self, url, *, params, timeout):
        super().get(url, params=params, timeout=timeout)
        result = self.responses[url]
        if isinstance(result, BaseException):
            raise result
        return result


class FakeForecastSource:
    """A `ForecastSource` backed by `{spot name: ForecastData or Exception}`."""

    def __init__(self, results):
        self.results = dict(results)
        self.fetched = []

    def fetch(self, spot):
        self.fetched.append(spot)
        result = self.results[spot.name]
        if isinstance(result, BaseException):
            raise result
        return result


class FakeJev:
    """A `JevLike` that records deep copies of its inputs."""

    def __init__(self, answers=None, exc=None):
        self.answers = answers
        self.exc = exc
        self.calls = []

    def ask(self, state, questions):
        self.calls.append((copy.deepcopy(state), copy.deepcopy(questions)))
        if self.exc is not None:
            raise self.exc
        return copy.deepcopy(self.answers)


class FakeNotifier:
    """A `Notifier` that appends every text to `sent`, then optionally raises."""

    def __init__(self, exc=None):
        self.exc = exc
        self.sent = []

    def send(self, text):
        self.sent.append(text)
        if self.exc is not None:
            raise self.exc
