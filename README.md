# Morya Mens Parlour + Sam

One FastAPI app that serves the salon website, the admin dashboard and Sam's booking engine.

## Run locally
```
python -m venv venv
venv\Scripts\activate          # Mac/Linux: source venv/bin/activate
pip install -r requirements.txt
set SECRET_KEY=any-long-random-text
set ADMIN_PASSWORD=choose-a-strong-password      # Mac/Linux: use export instead of set
uvicorn app.main:app --reload
```
Open http://127.0.0.1:8000 (website), /admin.html (dashboard, user: admin), /docs (API).
If you skip the two variables, the app makes temporary ones and prints the admin password in the terminal.

## Test
```
pytest
```

## Edit salon settings
`sam/config.py`: hours, closed days, chairs, services, durations, prices.

## Voice agent (Vapi)
Set `SAM_TOOL_KEY` to a long random text. Vapi calls `POST /api/voice/tools` and must send it in the
`X-Sam-Key` header (or as `Authorization: Bearer <key>`). Tools: get_wait_time, check_availability,
book_appointment, find_my_bookings, cancel_appointment.
