"""CallMeBot WhatsApp notifier (FR-13, spec 4.10, 6.4): TC-137 to TC-144, TC-208, TC-209."""

from __future__ import annotations

from urllib.parse import quote_plus

import pytest
import requests

from surf_alert.errors import NotificationError, SurfAlertError
from surf_alert.notify import CallMeBotNotifier
from tests.fakes import FakeResponse, FakeSession
from tests.scenario import PHONE_ERROR

CALLMEBOT_URL = "https://api.callmebot.com/whatsapp.php"
PHONE = "+34612345678"


def notifier_with(response=None, exc=None, api_key="SECRET", **kwargs):
    fake = FakeSession(response=response or FakeResponse(200, text="ok"), exc=exc)
    return CallMeBotNotifier(PHONE, api_key, session=fake, **kwargs), fake


def send_error(notifier, text="hi"):
    with pytest.raises(NotificationError) as ei:
        notifier.send(text)
    return ei.value


def test_tc137_request_shape():
    """TC-137: AC-13.1, one GET with the exact URL, params and 30 s timeout."""
    n, fake = notifier_with()
    n.send("hi")
    assert fake.calls == [
        {
            "method": "get",
            "url": CALLMEBOT_URL,
            "params": {"phone": PHONE, "text": "hi", "apikey": "SECRET"},
            "timeout": 30.0,
        }
    ]


def test_tc138_success_returns_none():
    """TC-138: AC-13.2, HTTP 200 means success."""
    n, _ = notifier_with()
    assert n.send("hi") is None


@pytest.mark.parametrize(
    "status, text, msg",
    [
        (203, "APIKey is invalid", "CallMeBot returned HTTP 203: APIKey is invalid"),
        (500, "x", "CallMeBot returned HTTP 500: x"),
        (201, "ok", "CallMeBot returned HTTP 201: ok"),
    ],
    ids=["203", "500", "201"],
)
def test_tc139_non_200_status(status, text, msg):
    """TC-139: AC-13.3, any status other than 200 is an error; no retries."""
    n, fake = notifier_with(FakeResponse(status, text=text))
    err = send_error(n)
    assert str(err) == msg
    assert isinstance(err, SurfAlertError)
    assert len(fake.calls) == 1


@pytest.mark.parametrize(
    "status, text, msg",
    [
        (500, "e" * 300, "CallMeBot returned HTTP 500: " + "e" * 200),
        (403, "bad key SECRET, SECRET", "CallMeBot returned HTTP 403: bad key ***, ***"),
    ],
    ids=["truncated", "redacted"],
)
def test_tc140_body_truncated_and_redacted(status, text, msg):
    """TC-140: spec 6.4 and AC-17.4, the body is redacted and truncated to 200 characters."""
    n, _ = notifier_with(FakeResponse(status, text=text))
    err = send_error(n)
    assert str(err) == msg
    assert "SECRET" not in str(err)


@pytest.mark.parametrize(
    "exc, msg",
    [
        (
            requests.ConnectionError("failed url=/whatsapp.php?apikey=SECRET"),
            "request to CallMeBot failed: failed url=/whatsapp.php?apikey=***",
        ),
        (requests.Timeout("SECRET timed out SECRET"), "request to CallMeBot failed: *** timed out ***"),
    ],
    ids=["connection", "timeout"],
)
def test_tc141_transport_errors_redacted(exc, msg):
    """TC-141: AC-13.4, request exceptions are wrapped and the key never appears."""
    n, _ = notifier_with(exc=exc)
    err = send_error(n)
    assert str(err) == msg
    assert "SECRET" not in str(err)


@pytest.mark.parametrize("text", ["", "   ", "\n\t"], ids=["empty", "spaces", "newline_tab"])
def test_tc142_empty_text(text):
    """TC-142: AC-13.5, blank text is rejected without an HTTP call."""
    n, fake = notifier_with()
    with pytest.raises(ValueError) as ei:
        n.send(text)
    assert str(ei.value) == "message text must not be empty"
    assert fake.calls == []


@pytest.mark.parametrize("phone", ["+34612345678", "+1234567", "+123456789012345"])
def test_tc143_valid_phones(phone):
    """TC-143: AC-13.6, valid international numbers construct."""
    CallMeBotNotifier(phone, "k", session=FakeSession())


@pytest.mark.parametrize(
    "phone",
    [
        "0612345678",
        "34612345678",
        "+0612345678",
        "+123456",
        "+1234567890123456",
        "+34 612 345 678",
        "+3461234567a",
        "",
    ],
)
def test_tc143_invalid_phones(phone):
    """TC-143: AC-13.6, an invalid phone number is rejected."""
    with pytest.raises(ValueError) as ei:
        CallMeBotNotifier(phone, "k", session=FakeSession())
    assert str(ei.value) == PHONE_ERROR


@pytest.mark.parametrize("key", ["", "   "], ids=["empty", "blank"])
def test_tc143_empty_api_key(key):
    """TC-143: AC-13.6, an empty or blank api key is rejected."""
    with pytest.raises(ValueError) as ei:
        CallMeBotNotifier(PHONE, key, session=FakeSession())
    assert str(ei.value) == "CALLMEBOT_API_KEY must not be empty"


def test_tc143_phone_checked_first():
    """TC-143: with both a bad phone and an empty key, the phone message wins."""
    with pytest.raises(ValueError) as ei:
        CallMeBotNotifier("0612345678", "", session=FakeSession())
    assert str(ei.value) == PHONE_ERROR


def test_tc144_text_passed_unchanged_and_timeout():
    """TC-144: spec 6.5, the text is passed as-is (requests encodes params); the timeout option is used."""
    n, fake = notifier_with(timeout=5.0)
    n.send("Pipeline & Sunset: 100% #1?")
    assert fake.calls[0]["params"]["text"] == "Pipeline & Sunset: 100% #1?"
    assert fake.calls[0]["timeout"] == 5.0


REDACTION = [
    (
        "redact_before_truncate",
        "SECRET",
        FakeResponse(500, text="e" * 195 + "SECRET" + "f" * 10),
        None,
        "CallMeBot returned HTTP 500: " + "e" * 195 + "***ff",
    ),
    (
        "quote_plus_body",
        "a b+c",
        FakeResponse(203, text="apikey=a+b%2Bc is invalid (a b+c)"),
        None,
        "CallMeBot returned HTTP 203: apikey=*** is invalid (***)",
    ),
    (
        "quote_plus_transport",
        "a b+c",
        None,
        requests.ConnectionError("failed url=/whatsapp.php?phone=%2B34612345678&apikey=a+b%2Bc"),
        "request to CallMeBot failed: failed url=/whatsapp.php?phone=%2B34612345678&apikey=***",
    ),
]


@pytest.mark.parametrize(
    "key, response, exc, msg",
    [(k, r, e, m) for _, k, r, e, m in REDACTION],
    ids=[n for n, _, _, _, _ in REDACTION],
)
def test_tc208_redaction_order_and_quote_plus(key, response, exc, msg):
    """TC-208: spec 6.4, redact the key and its quote_plus form first, then truncate."""
    fake = FakeSession(response=response or FakeResponse(200, text="ok"), exc=exc)
    n = CallMeBotNotifier(PHONE, key, session=fake)
    text = str(send_error(n))
    assert text == msg
    assert key not in text
    assert quote_plus(key) not in text


@pytest.mark.parametrize("phone", ["+34612345678\n", "+34612345678 "], ids=["newline", "space"])
def test_tc209_phone_fullmatch(phone):
    """TC-209: spec 4.10, the phone check uses re.fullmatch (a trailing newline is rejected)."""
    with pytest.raises(ValueError) as ei:
        CallMeBotNotifier(phone, "k", session=FakeSession())
    assert str(ei.value) == PHONE_ERROR
