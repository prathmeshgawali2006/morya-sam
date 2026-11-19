import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.main import app
from sam import tools
from sam.booking import BookingError

client = TestClient(app)
KEY = {"X-Sam-Key": "tool-key"}


def call(name, args, call_id="t1", caller=None):
    msg = {"type": "tool-calls", "toolCallList": [{"id": call_id, "name": name, "arguments": args}]}
    if caller:
        msg["call"] = {"customer": {"number": caller}}
    r = client.post("/api/voice/tools", json={"message": msg}, headers=KEY)
    assert r.status_code == 200
    return r.json()["results"][0]


BOOK = {"name": "Ravi", "phone": "9876543210", "service": "haircut", "date": "2030-01-08",
        "time": "10:00", "customer_confirmed": True}


def test_endpoint_requires_the_key():
    body = {"message": {"type": "tool-calls", "toolCallList": []}}
    assert client.post("/api/voice/tools", json=body).status_code == 401
    assert client.post("/api/voice/tools", json=body, headers={"X-Sam-Key": "wrong"}).status_code == 401
    assert client.post("/api/voice/tools", json=body, headers={"Authorization": "Bearer tool-key"}).status_code == 200


def test_other_vapi_events_are_ignored():
    r = client.post("/api/voice/tools", json={"message": {"type": "status-update"}}, headers=KEY)
    assert r.json() == {"results": []}


def test_hindi_and_english_service_words():
    assert tools.normalize_service("कटिंग") == "haircut"
    assert tools.normalize_service("दाढ़ी") == "beard styling"
    assert tools.normalize_service("कटिंग और दाढ़ी") == "haircut and beard"
    assert tools.normalize_service("Hair Cut") == "haircut"
    for bad in ("facial", "hair colour", "massage", "pizza"):
        with pytest.raises(BookingError):
            tools.normalize_service(bad)


def test_date_and_time_words():
    now = datetime(2026, 11, 3, 10, 0)
    assert tools.resolve_date("कल", now).isoformat() == "2026-11-04"
    assert tools.resolve_date("today", now).isoformat() == "2026-11-03"
    assert tools.parse_time("3:30 PM").strftime("%H:%M") == "15:30"
    with pytest.raises(BookingError):
        tools.resolve_date("next thing", now)


def test_phone_cleaning():
    assert tools.clean_phone("+91 98765 43210") == "9876543210"
    assert tools.clean_phone("09876543210") == "9876543210"
    with pytest.raises(BookingError):
        tools.clean_phone("12345")


def test_wait_and_availability_tools():
    w = json.loads(call("get_wait_time", {"service": "कटिंग"})["result"])
    assert w["service"] == "haircut" and "wait_minutes" in w
    a = json.loads(call("check_availability", {"service": "haircut", "date": "2030-01-08", "after": "18:00"})["result"])
    assert a["free_times"][0] == "18:00" and a["free_times_say"][0] == "6:00 PM"
    monday = json.loads(call("check_availability", {"service": "haircut", "date": "2030-01-07"})["result"])
    assert monday["closed_that_day"] and monday["total_free"] == 0


def test_booking_needs_clear_confirmation():
    r = call("book_appointment", {**BOOK, "customer_confirmed": False})
    assert "error" in r and "yes" in r["error"].lower()


def test_book_then_retry_does_not_double_book():
    first = call("book_appointment", BOOK, call_id="same-id")
    again = call("book_appointment", BOOK, call_id="same-id")
    assert json.loads(first["result"])["status"] == "booked"
    assert first["result"] == again["result"]
    rows = client.get("/api/availability", params={"day": "2030-01-08", "service": "haircut"}).json()["free_times"]
    assert "10:00" in rows   # only 1 of 3 chairs used


def test_caller_number_is_used_when_phone_not_given():
    args = {k: v for k, v in BOOK.items() if k != "phone"}
    assert "result" in call("book_appointment", args, caller="+919123456780")
    found = json.loads(call("find_my_bookings", {}, call_id="t2", caller="+919123456780")["result"])
    assert found["count"] == 1 and found["bookings"][0]["time_say"] == "10:00 AM"


def test_full_slot_and_closed_day_errors_are_readable():
    for i in range(3):
        assert "result" in call("book_appointment", {**BOOK, "phone": f"900000000{i}"}, call_id=f"f{i}")
    r = call("book_appointment", {**BOOK, "phone": "9111111111"}, call_id="f9")
    assert "full" in r["error"].lower()
    r = call("book_appointment", {**BOOK, "date": "2030-01-07"}, call_id="m1")
    assert "closed" in r["error"].lower()


def test_cancel_flow_checks_phone():
    b = json.loads(call("book_appointment", BOOK, call_id="c1")["result"])
    wrong = call("cancel_appointment", {"booking_id": b["booking_id"], "phone": "9000000000", "customer_confirmed": True}, call_id="c2")
    assert "error" in wrong
    ok = call("cancel_appointment", {"booking_id": b["booking_id"], "phone": "9876543210", "customer_confirmed": True}, call_id="c3")
    assert json.loads(ok["result"])["status"] == "cancelled"


def test_unknown_tool_is_reported_not_crashed():
    assert "error" in call("make_coffee", {})
