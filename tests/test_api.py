from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
BOOKING = {"name": "Ravi", "phone": "9444444444", "email": "", "date": "2030-01-08",
           "time": "10:00", "service": "haircut", "notes": ""}   # 2030-01-08 is a Tuesday


def admin_headers():
    r = client.post("/api/admin/login", json={"username": "admin", "password": "test-pass"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_no_default_admin_password():
    r = client.post("/api/admin/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 401


def test_admin_endpoints_need_login():
    assert client.get("/api/appointments").status_code == 401
    assert client.post("/api/walkin", json={"service": "haircut"}).status_code == 401
    assert client.get("/api/messages").status_code == 401


def test_website_is_served():
    r = client.get("/")
    assert r.status_code == 200 and "MORYA" in r.text.upper()
    assert "localhost" not in client.get("/contacts.html").text


def test_public_availability_and_wait():
    r = client.get("/api/availability", params={"day": "2030-01-08", "service": "haircut"})
    assert r.json()["free_times"][0] == "07:30"
    assert client.get("/api/availability", params={"day": "2030-01-07", "service": "haircut"}).json()["free_times"] == []  # Monday
    assert "wait_minutes" in client.get("/api/wait", params={"service": "haircut"}).json()


def test_booking_flow_website_to_admin():
    assert client.post("/api/appointments", json=BOOKING).status_code == 200
    free = client.get("/api/availability", params={"day": "2030-01-08", "service": "haircut"}).json()["free_times"]
    assert "10:00" in free  # 3 chairs, 1 used
    h = admin_headers()
    rows = client.get("/api/appointments", headers=h).json()
    assert rows[0]["name"] == "Ravi" and rows[0]["time"] == "10:00 AM" and rows[0]["status"] == "confirmed"
    assert client.put(f"/api/appointments/{rows[0]['id']}/status", json={"status": "completed"}, headers=h).status_code == 200
    assert client.put(f"/api/appointments/{rows[0]['id']}/status", json={"status": "nonsense"}, headers=h).status_code == 400
    assert client.delete(f"/api/appointments/{rows[0]['id']}", headers=h).status_code == 200


def test_bad_bookings_get_readable_errors():
    r = client.post("/api/appointments", json={**BOOKING, "service": "spa day"})
    assert r.status_code == 400 and "don't offer" in r.json()["detail"]
    r = client.post("/api/appointments", json={**BOOKING, "time": "banana"})
    assert r.status_code == 400
    r = client.post("/api/appointments", json={**BOOKING, "date": "2030-01-07"})   # Monday
    assert r.status_code == 400 and "closed" in r.json()["detail"].lower()


def test_full_slot_is_rejected():
    for i in range(3):
        assert client.post("/api/appointments", json={**BOOKING, "phone": f"900000000{i}"}).status_code == 200
    r = client.post("/api/appointments", json={**BOOKING, "phone": "9111111111"})
    assert r.status_code == 400 and "full" in r.json()["detail"].lower()


def test_review_and_message_still_work():
    assert client.post("/api/reviews", json={"name": "A", "rating": 5, "review": "Great"}).status_code == 200
    assert client.post("/api/messages", json={"name": "A", "email": "a@b.c", "subject": "Hi", "message": "Hello"}).status_code == 200
    assert len(client.get("/api/reviews", headers=admin_headers()).json()) == 1
