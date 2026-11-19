"""Voice-agent tools. The voice platform (Vapi) calls these over HTTP; all booking rules stay in booking.py.

Design rules: short plain results, errors that say what to do next, no double bookings
when the platform retries a call, and no booking unless the caller clearly confirmed.
"""
import json
import logging
import re
from datetime import date, datetime, timedelta

from . import booking, config
from .booking import BookingError
from .db import connect

log = logging.getLogger("sam.tools")

# words callers may use (English and Hindi, spelled either way)
HAIR = ("haircut", "hair cut", "hair", "cutting", "cut", "कटिंग", "बाल", "हेयर")
BEARD = ("beard", "दाढ़ी", "दाढी", "dadhi", "daadhi", "बियर्ड")
NOT_BOOKABLE = ("color", "colour", "keratin", "straight", "perm", "facial", "massage", "scrub",
                "mask", "tan", "रंग", "कलर", "फेशियल", "मसाज", "स्ट्रेट")
DAY_WORDS = {"today": 0, "aaj": 0, "आज": 0, "tomorrow": 1, "kal": 1, "कल": 1,
             "day after tomorrow": 2, "parso": 2, "परसों": 2}


def normalize_service(text: str | None) -> str:
    t = (text or "").strip().lower()
    if t in config.SERVICES:
        return t
    if any(w in t for w in NOT_BOOKABLE):
        raise BookingError("That service can't be booked by phone yet. Ask the caller to visit or call the salon.")
    hair, beard = any(w in t for w in HAIR), any(w in t for w in BEARD)
    if hair and beard:
        return "haircut and beard"
    if hair:
        return "haircut"
    if beard:
        return "beard styling"
    raise BookingError(f"I can only book these: {', '.join(config.SERVICES)}.")


def resolve_date(text: str | None, now: datetime) -> date:
    t = (text or "").strip().lower()
    if t in DAY_WORDS:
        return now.date() + timedelta(days=DAY_WORDS[t])
    try:
        return date.fromisoformat(t)
    except ValueError:
        raise BookingError("Give the date as YYYY-MM-DD (work it out from today's date), or say today or tomorrow.")


def parse_time(text: str | None):
    t = (text or "").strip().upper()
    for fmt in ("%H:%M", "%I:%M %p", "%I:%M%p", "%I %p", "%I%p"):
        try:
            return datetime.strptime(t, fmt).time()
        except ValueError:
            pass
    raise BookingError("Give the time as HH:MM in 24-hour format, for example 15:30.")


def spoken(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


def clean_phone(text: str | None) -> str:
    digits = re.sub(r"\D", "", text or "")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) != 10:
        raise BookingError("Ask the caller to say their 10-digit phone number again.")
    return digits


def _confirmed(a: dict) -> bool:
    return str(a.get("customer_confirmed")).strip().lower() in ("true", "yes", "1")


def _say_slot(nxt):
    if not nxt:
        return None
    dt = datetime.fromisoformat(f"{nxt['date']}T{nxt['time']}")
    return {"date": nxt["date"], "weekday": dt.strftime("%A"), "time_say": spoken(dt)}


# ---------------- the tools ----------------
def get_wait_time(a: dict, ctx: dict) -> dict:
    svc, now = normalize_service(a.get("service")), ctx["now"]
    return {"service": svc, "salon_open_now": booking.is_open(now),
            "wait_minutes": booking.wait_minutes(svc, now=now),
            "next_free": _say_slot(booking.next_available(svc, now=now))}


def check_availability(a: dict, ctx: dict) -> dict:
    svc, now = normalize_service(a.get("service")), ctx["now"]
    day = resolve_date(a.get("date"), now)
    free = booking.check_availability(day, svc, now=now)
    if a.get("after"):
        after = parse_time(a["after"]).strftime("%H:%M")
        free = [t for t in free if t >= after]
    shown = free[:10]
    return {"date": day.isoformat(), "weekday": day.strftime("%A"), "service": svc,
            "closed_that_day": day.weekday() in config.CLOSED_WEEKDAYS, "total_free": len(free),
            "free_times": shown,
            "free_times_say": [spoken(datetime.combine(day, parse_time(t))) for t in shown]}


def book_appointment(a: dict, ctx: dict) -> dict:
    if not _confirmed(a):
        raise BookingError("Read the service, date, time and name back to the caller and get a clear yes. "
                           "Then call this again with customer_confirmed true.")
    name = (a.get("name") or "").strip()
    if not name:
        raise BookingError("Ask the caller for their name.")
    phone = clean_phone(a.get("phone") or ctx.get("caller"))
    svc, day = normalize_service(a.get("service")), resolve_date(a.get("date"), ctx["now"])
    start = datetime.combine(day, parse_time(a.get("time")))
    b = booking.book(name, phone, svc, start, now=ctx["now"])
    end = datetime.fromisoformat(b["end"])
    return {"status": "booked", "booking_id": b["id"], "name": name, "service": svc,
            "date": day.isoformat(), "weekday": day.strftime("%A"),
            "time_say": spoken(start), "ends_say": spoken(end)}


def find_my_bookings(a: dict, ctx: dict) -> dict:
    phone = clean_phone(a.get("phone") or ctx.get("caller"))
    rows = booking.find_bookings(phone, now=ctx["now"])
    out = []
    for r in rows:
        s = datetime.fromisoformat(r["start"])
        out.append({"booking_id": r["id"], "service": r["service"], "date": s.date().isoformat(),
                    "weekday": s.strftime("%A"), "time_say": spoken(s)})
    return {"count": len(out), "bookings": out}


def cancel_appointment(a: dict, ctx: dict) -> dict:
    if not _confirmed(a):
        raise BookingError("Ask the caller to confirm the cancellation, then call again with customer_confirmed true.")
    try:
        booking_id = int(a.get("booking_id"))
    except (TypeError, ValueError):
        raise BookingError("Look up the caller's bookings first to get the booking_id.")
    booking.cancel(booking_id, clean_phone(a.get("phone") or ctx.get("caller")))
    return {"status": "cancelled", "booking_id": booking_id}


TOOLS = {"get_wait_time": get_wait_time, "check_availability": check_availability,
         "book_appointment": book_appointment, "find_my_bookings": find_my_bookings,
         "cancel_appointment": cancel_appointment}
MUTATING = {"book_appointment", "cancel_appointment"}


# ---------------- request handling ----------------
def _args(tc: dict) -> dict:
    a = tc.get("arguments") or tc.get("parameters") or (tc.get("function") or {}).get("arguments") or {}
    if isinstance(a, str):
        try:
            a = json.loads(a)
        except ValueError:
            a = {}
    return a if isinstance(a, dict) else {}


def _cached(call_id: str):
    conn = connect()
    row = conn.execute("SELECT result FROM tool_calls WHERE id=?", (call_id,)).fetchone()
    conn.close()
    return row["result"] if row else None


def _store(call_id: str, result: str) -> None:
    conn = connect()
    conn.execute("INSERT OR IGNORE INTO tool_calls (id, result) VALUES (?, ?)", (call_id, result))
    conn.close()


def handle(message: dict, now: datetime | None = None) -> dict:
    """Turn a Vapi 'tool-calls' message into the {'results': [...]} reply Vapi expects."""
    ctx = {"now": now or booking._now(),
           "caller": ((message.get("call") or {}).get("customer") or {}).get("number")}
    results = []
    for tc in message.get("toolCallList") or message.get("toolCalls") or []:
        call_id = tc.get("id")
        name = tc.get("name") or (tc.get("function") or {}).get("name")
        fn = TOOLS.get(name)
        if fn is None:
            results.append({"toolCallId": call_id, "error": f"Unknown tool: {name}"})
            continue
        if name in MUTATING and call_id:      # the platform may retry: return the first outcome
            first = _cached(call_id)
            if first:
                results.append({"toolCallId": call_id, "result": first})
                continue
        try:
            out = json.dumps(fn(_args(tc), ctx), ensure_ascii=False)
        except BookingError as e:
            results.append({"toolCallId": call_id, "error": str(e)})
            continue
        except Exception:
            log.exception("tool %s failed", name)
            results.append({"toolCallId": call_id,
                            "error": "Something went wrong. Apologise and ask the caller to try again."})
            continue
        if name in MUTATING and call_id:
            _store(call_id, out)
        results.append({"toolCallId": call_id, "result": out})
    return {"results": results}
