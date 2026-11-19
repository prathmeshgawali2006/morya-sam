"""Salon settings. Anything marked TODO still needs the real value from the owner."""
from datetime import time

SALON_NAME = "Morya Mens Parlour"
TIMEZONE = "Asia/Kolkata"   # the server may run in UTC, the salon runs on IST
OPEN_TIME = time(7, 30)
CLOSE_TIME = time(23, 0)
CLOSED_WEEKDAYS = {0}   # 0 = Monday (closed)
CHAIRS = 3              # 3 barbers, so 3 customers at the same time
SLOT_MINUTES = 15       # bookings start on 15-minute marks, counted from opening time

# Durations use the longer end of the owner's range, so customers are never kept waiting.
# price is None until the owner confirms it: Sam must never quote a price that isn't filled in.
SERVICES = {
    "haircut": {"minutes": 40, "price": None},            # 35-40 min
    "beard styling": {"minutes": 25, "price": None},      # 20-25 min
    "haircut and beard": {"minutes": 65, "price": None},  # TODO confirm with real timing
}
