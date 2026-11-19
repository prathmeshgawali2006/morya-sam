"""Core booking logic. No AI in here: this is the part that must never be wrong."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import config
from .db import connect


class BookingError(Exception):
    """A booking problem that is safe to read out to a customer."""


def _now() -> datetime:
    """Current salon time (naive), independent of the server's timezone."""
    return datetime.now(ZoneInfo(config.TIMEZONE)).replace(tzinfo=None)


def list_services() -> dict:
    return config.SERVICES


def _fmt(dt: datetime) -> str:
    return dt.isoformat(timespec="minutes")


def _service(name: str) -> dict:
    key = name.strip().lower()
    if key not in config.SERVICES:
        raise BookingError(f"We don't offer '{name}'. We offer: {', '.join(config.SERVICES)}.")
    return config.SERVICES[key]


def _bounds(day: date):
    """(open, close) datetimes for a day, or None if the salon is closed that day."""
    if day.weekday() in config.CLOSED_WEEKDAYS:
        return None
    return (datetime.combine(day, config.OPEN_TIME), datetime.combine(day, config.CLOSE_TIME))


def _in_hours(start: datetime, end: datetime) -> bool:
    b = _bounds(start.date())
    return b is not None and start >= b[0] and end <= b[1]


def _has_room(conn, start: datetime, end: datetime) -> bool:
    """True if one more customer fits in [start, end) without exceeding the number of chairs."""
    rows = conn.execute(
        "SELECT start, end FROM bookings WHERE status='confirmed' AND start < ? AND end > ?",
        (_fmt(end), _fmt(start)),
    ).fetchall()
    existing = [(datetime.fromisoformat(r["start"]), datetime.fromisoformat(r["end"])) for r in rows]
    # the load only increases when a booking starts, so checking those moments is enough
    checkpoints = [start] + [s for s, _ in existing if s > start]
    for p in checkpoints:
        active = sum(1 for s, e in existing if s <= p < e)
        if active + 1 > config.CHAIRS:
            return False
    return True


def is_open(now: datetime | None = None) -> bool:
    """True if the salon is open at this moment."""
    now = now or _now()
    bounds = _bounds(now.date())
    return bounds is not None and bounds[0] <= now < bounds[1]


def check_availability(day: date, service: str, now: datetime | None = None) -> list[str]:
    """Free start times (HH:MM) for this service on this day."""
    now = now or _now()
    minutes = _service(service)["minutes"]
    bounds = _bounds(day)
    if bounds is None:
        return []
    conn = connect()
    free = []
    slot = bounds[0]
    while slot + timedelta(minutes=minutes) <= bounds[1]:
        end = slot + timedelta(minutes=minutes)
        if slot > now and _has_room(conn, slot, end):
            free.append(slot.strftime("%H:%M"))
        slot += timedelta(minutes=config.SLOT_MINUTES)
    conn.close()
    return free


def next_available(service: str, now: datetime | None = None, days: int = 7) -> dict | None:
    """Earliest bookable time from now, looking up to `days` days ahead."""
    now = now or _now()
    for offset in range(days + 1):
        day = (now + timedelta(days=offset)).date()
        free = check_availability(day, service, now=now)
        if free:
            return {"date": day.isoformat(), "time": free[0]}
    return None


def wait_minutes(service: str, now: datetime | None = None) -> int | None:
    """How long until a barber can start this service. 0 = a chair is free right now."""
    now = now or _now()
    minutes = _service(service)["minutes"]
    now = now.replace(second=0, microsecond=0)
    end = now + timedelta(minutes=minutes)
    if _in_hours(now, end):
        conn = connect()
        room = _has_room(conn, now, end)
        conn.close()
        if room:
            return 0
    nxt = next_available(service, now=now)
    if nxt is None:
        return None
    start = datetime.fromisoformat(f"{nxt['date']}T{nxt['time']}")
    return int((start - now).total_seconds() // 60)


def book(name: str, phone: str, service: str, start: datetime, now: datetime | None = None,
         email: str = "", notes: str = "") -> dict:
    now = now or _now()
    name, phone = name.strip(), phone.strip()
    if not name or not phone:
        raise BookingError("We need a name and a phone number to book.")
    minutes = _service(service)["minutes"]
    end = start + timedelta(minutes=minutes)
    if start <= now:
        raise BookingError("That time has already passed.")
    bounds = _bounds(start.date())
    if bounds is None:
        raise BookingError("We are closed on that day.")
    if not _in_hours(start, end):
        raise BookingError(
            f"We are open {config.OPEN_TIME.strftime('%H:%M')} to {config.CLOSE_TIME.strftime('%H:%M')}, "
            "and the service must finish before closing."
        )
    offset = int((start - bounds[0]).total_seconds() // 60)
    if offset % config.SLOT_MINUTES != 0 or start.second != 0:
        raise BookingError(f"Bookings start on {config.SLOT_MINUTES}-minute marks.")
    return _insert(name, phone, service, start, end, email, notes)


def add_walkin(service: str, now: datetime | None = None) -> dict:
    """Log a walk-in who starts now, so wait-time answers stay accurate."""
    now = (now or _now()).replace(second=0, microsecond=0)
    end = now + timedelta(minutes=_service(service)["minutes"])
    if not _in_hours(now, end):
        raise BookingError("The salon is not open right now.")
    return _insert("Walk-in", "walk-in", service, now, end)


def _insert(name: str, phone: str, service: str, start: datetime, end: datetime,
            email: str = "", notes: str = "") -> dict:
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")  # lock so two callers can't take the last chair
        if not _has_room(conn, start, end):
            conn.execute("ROLLBACK")
            raise BookingError("Sorry, that time is already full.")
        cur = conn.execute(
            "INSERT INTO bookings (name, phone, service, start, end, email, notes) VALUES (?,?,?,?,?,?,?)",
            (name, phone, service.strip().lower(), _fmt(start), _fmt(end), email.strip(), notes.strip()),
        )
        conn.execute("COMMIT")
        return {"id": cur.lastrowid, "name": name, "service": service.strip().lower(),
                "start": _fmt(start), "end": _fmt(end)}
    finally:
        conn.close()


def find_bookings(phone: str, now: datetime | None = None) -> list[dict]:
    now = now or _now()
    conn = connect()
    rows = conn.execute(
        "SELECT id, name, service, start, end FROM bookings "
        "WHERE phone=? AND status='confirmed' AND start > ? ORDER BY start",
        (phone.strip(), _fmt(now)),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def cancel(booking_id: int, phone: str) -> None:
    """Cancel only if the phone number matches the one used to book."""
    conn = connect()
    cur = conn.execute(
        "UPDATE bookings SET status='cancelled' WHERE id=? AND phone=? AND status='confirmed'",
        (booking_id, phone.strip()),
    )
    conn.close()
    if cur.rowcount == 0:
        raise BookingError("We couldn't find that booking for this phone number.")


# ---- admin helpers (used by the website dashboard)
VALID_STATUSES = {"pending", "confirmed", "completed", "cancelled"}


def list_all() -> list[dict]:
    conn = connect()
    rows = conn.execute("SELECT * FROM bookings ORDER BY start DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_status(booking_id: int, status: str) -> None:
    if status not in VALID_STATUSES:
        raise BookingError("Invalid status.")
    conn = connect()
    cur = conn.execute("UPDATE bookings SET status=? WHERE id=?", (status, booking_id))
    conn.close()
    if cur.rowcount == 0:
        raise BookingError("Booking not found.")


def delete(booking_id: int) -> None:
    conn = connect()
    cur = conn.execute("DELETE FROM bookings WHERE id=?", (booking_id,))
    conn.close()
    if cur.rowcount == 0:
        raise BookingError("Booking not found.")
