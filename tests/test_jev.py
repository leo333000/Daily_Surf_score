"""Jev HTTP client (FR-9, spec 4.7, 6.3): TC-96 to TC-104 and TC-207."""

from __future__ import annotations

from urllib.parse import quote_plus

import pytest
import requests

from surf_alert.errors import JevError
from surf_alert.jev import JevClient
from tests.fakes import FakeResponse, FakeSession
from tests.scenario import REF_ANSWERS, REF_JEV_RESPONSE, REF_QUESTIONS, REF_STATE

JEV_URL = "https://api.typesafe.ai/v1/systemone"
Q = {"good_spot_1": {"type": "noul", "instructions": "x"}}


def client_with(response=None, exc=None, api_key="k"):
    fake = FakeSession(response=response, exc=exc)
    return JevClient(api_key, model="jev-latest", session=fake), fake


def jev_error(client, state=None, questions=None):
    with pytest.raises(JevError) as ei:
        client.ask({"a": 1} if state is None else state, Q if questions is None else questions)
    return ei.value


def test_tc096_request_shape():
    """TC-96: AC-9.1, one POST with the exact URL, headers, JSON body and 30 s timeout."""
    client, fake = client_with(FakeResponse(200, REF_JEV_RESPONSE))
    client.ask(REF_STATE, REF_QUESTIONS)
    assert fake.calls == [
        {
            "method": "post",
            "url": JEV_URL,
            "headers": {"Authorization": "Bearer k", "Content-Type": "application/json"},
            "json": {"model": "jev-latest", "state": REF_STATE, "questions": REF_QUESTIONS},
            "timeout": 30.0,
        }
    ]


def test_tc097_options():
    """TC-97: the api key, model and timeout options are used."""
    fake = FakeSession(response=FakeResponse(200, REF_JEV_RESPONSE))
    JevClient("k2", model="jev-x", session=fake, timeout=5.0).ask({"a": 1}, Q)
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["headers"]["Authorization"] == "Bearer k2"
    assert call["json"]["model"] == "jev-x"
    assert call["timeout"] == 5.0


@pytest.mark.parametrize(
    "response, expected",
    [
        (FakeResponse(200, REF_JEV_RESPONSE), REF_ANSWERS),
        (FakeResponse(201, REF_JEV_RESPONSE), REF_ANSWERS),
        (FakeResponse(200, {"answers": {}}), {}),
    ],
    ids=["200", "201", "empty_answers"],
)
def test_tc098_returns_answers(response, expected):
    """TC-98: AC-9.2, a 2xx JSON body returns its `answers` object."""
    client, _ = client_with(response)
    assert client.ask({"a": 1}, Q) == expected


TABLE_F = [
    (401, "unauthorized", "authentication failed (HTTP 401); check TYPESAFE_API_KEY"),
    (422, '{"detail":"bad"}', 'invalid request (HTTP 422): {"detail":"bad"}'),
    (429, "slow down", "rate limited (HTTP 429)"),
    (529, "busy", "service overloaded (HTTP 529)"),
    (500, "oops", "unexpected response (HTTP 500): oops"),
    (503, "", "unexpected response (HTTP 503): "),
    (302, "moved", "unexpected response (HTTP 302): moved"),
    (404, "nf", "unexpected response (HTTP 404): nf"),
]


@pytest.mark.parametrize("status, text, msg", TABLE_F, ids=[str(r[0]) for r in TABLE_F])
def test_tc099_http_errors(status, text, msg):
    """TC-99: AC-9.3, table F, non-2xx statuses map to JevError messages; no retries."""
    client, fake = client_with(FakeResponse(status, text=text))
    err = jev_error(client)
    assert str(err) == msg
    assert err.status_code == status
    assert len(fake.calls) == 1


@pytest.mark.parametrize(
    "status, text, msg",
    [
        (422, "a" * 250, "invalid request (HTTP 422): " + "a" * 200),
        (500, "b" * 201, "unexpected response (HTTP 500): " + "b" * 200),
    ],
    ids=["422", "500"],
)
def test_tc100_body_truncated(status, text, msg):
    """TC-100: {body} is truncated to 200 characters."""
    client, _ = client_with(FakeResponse(status, text=text))
    assert str(jev_error(client)) == msg


@pytest.mark.parametrize(
    "exc, msg",
    [
        (requests.Timeout("t"), "request failed: t"),
        (requests.ConnectionError("conn refused"), "request failed: conn refused"),
        (requests.RequestException("x"), "request failed: x"),
    ],
    ids=["timeout", "connection", "request_exception"],
)
def test_tc101_transport_errors(exc, msg):
    """TC-101: AC-9.4, request exceptions become 'request failed: ...' with no status."""
    client, _ = client_with(exc=exc)
    err = jev_error(client)
    assert str(err) == msg
    assert err.status_code is None


NOT_JSON = "malformed response: body is not valid JSON"
NO_ANSWERS = "malformed response: missing 'answers' object"

TABLE_G = [
    ("value_error", FakeResponse(200, json_exc=ValueError("x")), NOT_JSON, 200),
    (
        "requests_json_decode_error",
        FakeResponse(200, json_exc=requests.exceptions.JSONDecodeError("Expecting value", "<html>", 0)),
        NOT_JSON,
        200,
    ),
    ("value_error_201", FakeResponse(201, json_exc=ValueError("x")), NOT_JSON, 201),
    ("list_body", FakeResponse(200, []), NO_ANSWERS, 200),
    ("str_body", FakeResponse(200, "answers"), NO_ANSWERS, 200),
    ("no_answers_key", FakeResponse(200, {"model": "jev-1.13.0"}), NO_ANSWERS, 200),
    ("answers_list", FakeResponse(200, {"answers": []}), NO_ANSWERS, 200),
    ("answers_none", FakeResponse(200, {"answers": None}), NO_ANSWERS, 200),
]


@pytest.mark.parametrize(
    "response, msg, code",
    [(r, m, c) for _, r, m, c in TABLE_G],
    ids=[n for n, _, _, _ in TABLE_G],
)
def test_tc102_malformed_bodies(response, msg, code):
    """TC-102: AC-9.5, table G, malformed 2xx bodies."""
    client, _ = client_with(response)
    err = jev_error(client)
    assert str(err) == msg
    assert err.status_code == code


@pytest.mark.parametrize("key", ["", "   "], ids=["empty", "blank"])
def test_tc103_empty_api_key(key):
    """TC-103: AC-9.6, an empty or blank api key is rejected."""
    with pytest.raises(ValueError) as ei:
        JevClient(key, session=FakeSession())
    assert str(ei.value) == "api_key must not be empty"


def test_tc103_empty_questions():
    """TC-103: AC-9.6, empty questions are rejected without an HTTP call."""
    client, fake = client_with(FakeResponse(200, REF_JEV_RESPONSE))
    with pytest.raises(ValueError) as ei:
        client.ask({"a": 1}, {})
    assert str(ei.value) == "questions must not be empty"
    assert fake.calls == []


def test_tc104_unexpected_exception_propagates():
    """TC-104: spec 6.5, exceptions that are not request errors propagate."""
    client, _ = client_with(exc=RuntimeError("bug"))
    with pytest.raises(RuntimeError) as ei:
        client.ask({"a": 1}, Q)
    assert str(ei.value) == "bug"


REDACTION = [
    ("body_422", "SECRETKEY", FakeResponse(422, text="bad token SECRETKEY here"), None,
     "invalid request (HTTP 422): bad token *** here"),
    ("body_500_twice", "SECRETKEY", FakeResponse(500, text="echo SECRETKEY SECRETKEY"), None,
     "unexpected response (HTTP 500): echo *** ***"),
    ("redact_before_truncate", "SECRETKEY", FakeResponse(422, text="a" * 195 + "SECRETKEY" + "b" * 10), None,
     "invalid request (HTTP 422): " + "a" * 195 + "***bb"),
    ("transport", "SECRETKEY", None, requests.ConnectionError("Authorization: Bearer SECRETKEY rejected"),
     "request failed: Authorization: Bearer *** rejected"),
    ("quote_plus_body", "a b+c", FakeResponse(422, text="k=a+b%2Bc raw=a b+c"), None,
     "invalid request (HTTP 422): k=*** raw=***"),
    ("quote_plus_transport", "a b+c", None, requests.Timeout("u?key=a+b%2Bc"),
     "request failed: u?key=***"),
]


@pytest.mark.parametrize(
    "key, response, exc, msg",
    [(k, r, e, m) for _, k, r, e, m in REDACTION],
    ids=[n for n, _, _, _, _ in REDACTION],
)
def test_tc207_redaction(key, response, exc, msg):
    """TC-207: spec 6.3 and AC-17.4, the key and its quote_plus form are redacted, then truncated."""
    client, _ = client_with(response=response, exc=exc, api_key=key)
    text = str(jev_error(client))
    assert text == msg
    assert key not in text
    assert quote_plus(key) not in text
