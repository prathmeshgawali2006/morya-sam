from datetime import date, datetime

import pytest

from sam import booking, config
from sam.booking import BookingError

NOW = datetime(2026, 11, 1, 9, 0)   # Sunday
DAY = date(2026, 11, 10)            # Tuesday (open)
MONDAY = date(2026, 11, 2)          # closed


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("SAM_DB", str(tmp_path / "test.db"))


def at(hour, minute=0, day=10):
    return datetime(2026, 11, day, hour, minute)


def test_empty_day_slots_follow_opening_hours():
    slots = booking.check_availability(DAY, "haircut", now=NOW)
    assert slots[0] == "07:30" and slots[-1] == "22:15" and len(slots) == 60


def test_long_service_cannot_start_near_closing():
    assert booking.check_availability(DAY, "haircut and beard", now=NOW)[-1] == "21:45"


def test_closed_on_monday():
    assert booking.check_availability(MONDAY, "haircut", now=NOW) == []
    with pytest.raises(BookingError):
        booking.book("A", "1", "haircut", at(10, day=2), now=NOW)


def test_slot_fills_at_three_chairs():
    for i in range(config.CHAIRS):
        booking.book(f"Cust{i}", f"900000000{i}", "haircut", at(15), now=NOW)
    assert "15:00" not in booking.check_availability(DAY, "haircut", now=NOW)
    with pytest.raises(BookingError):
        booking.book("Late", "9111111111", "haircut", at(15), now=NOW)


def test_overlap_blocks_neighbouring_slots_only():
    for i in range(config.CHAIRS):
        booking.book(f"C{i}", f"90000000{i}0", "haircut and beard", at(12), now=NOW)  # 12:00-13:05
    slots = booking.check_availability(DAY, "haircut", now=NOW)
    for blocked in ("11:30", "12:00", "12:45", "13:00"):
        assert blocked not in slots
    assert "11:15" in slots and "13:15" in slots


def test_staggered_bookings_use_real_concurrency():
    # A and B overlap 10:00-10:40, D starts 10:15, C starts 10:45: 10:15 is the first moment all 3 chairs are busy
    booking.book("A", "1", "haircut", at(10), now=NOW)
    booking.book("B", "2", "haircut", at(10), now=NOW)
    booking.book("C", "3", "haircut", at(10, 45), now=NOW)
    booking.book("D", "4", "haircut", at(10, 15), now=NOW)  # third chair from 10:15
    assert "10:15" not in booking.check_availability(DAY, "haircut", now=NOW)


def test_rejects_bad_requests():
    with pytest.raises(BookingError):
        booking.book("A", "1", "haircut", datetime(2026, 10, 31, 12), now=NOW)   # past
    with pytest.raises(BookingError):
        booking.book("A", "1", "haircut", at(6), now=NOW)                         # before opening
    with pytest.raises(BookingError):
        booking.book("A", "1", "haircut", at(22, 30), now=NOW)                    # ends after close
    with pytest.raises(BookingError):
        booking.book("A", "1", "haircut", at(10, 10), now=NOW)                    # off the 15-min grid
    with pytest.raises(BookingError):
        booking.book("A", "1", "spa day", at(12), now=NOW)                        # unknown service


def test_cancel_needs_matching_phone_and_frees_slot():
    b = booking.book("Ravi", "9222222222", "haircut", at(11), now=NOW)
    with pytest.raises(BookingError):
        booking.cancel(b["id"], "0000000000")
    booking.cancel(b["id"], "9222222222")
    assert booking.find_bookings("9222222222", now=NOW) == []


def test_next_available_skips_full_slots_and_closed_monday():
    now = at(8, 0)
    for i in range(config.CHAIRS):
        booking.book(f"C{i}", f"91{i}", "haircut", at(8, 15), now=NOW)
    assert booking.next_available("haircut", now=now) == {"date": "2026-11-10", "time": "09:00"}
    after_close = datetime(2026, 11, 1, 23, 30)  # Sunday night, Monday is closed
    assert booking.next_available("haircut", now=after_close) == {"date": "2026-11-03", "time": "07:30"}


def test_wait_minutes_and_walkins():
    now = at(10, 5)
    assert booking.wait_minutes("haircut", now=now) == 0
    for i in range(config.CHAIRS):
        booking.add_walkin("haircut", now=now)
    assert booking.wait_minutes("haircut", now=now) == 40   # first chair frees at 10:45
    with pytest.raises(BookingError):
        booking.add_walkin("haircut", now=now)
    with pytest.raises(BookingError):
        booking.add_walkin("haircut", now=at(23, 30))
