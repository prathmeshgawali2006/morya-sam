"""Morya Mens Parlour: website + admin dashboard + Sam booking engine in one FastAPI app."""
import hmac
import os
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import bcrypt
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from fastapi.staticfiles import StaticFiles
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy import Column, DateTime, Integer, String, Text, create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from sam import booking, tools
from sam.booking import BookingError

ROOT = Path(__file__).resolve().parent.parent

# ---- secrets come from the environment, never from the code
SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    SECRET_KEY = secrets.token_hex(32)
    print("WARNING: SECRET_KEY not set. Using a temporary one (admin logins reset on restart).")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
if not ADMIN_PASSWORD:
    ADMIN_PASSWORD = secrets.token_urlsafe(9)
    print(f"WARNING: ADMIN_PASSWORD not set. Temporary admin password for this run: {ADMIN_PASSWORD}")
ALGORITHM = "HS256"
TOKEN_MINUTES = 1440

engine = create_engine(os.environ.get("DATABASE_URL", f"sqlite:///{ROOT / 'morya.db'}"),
                       connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
app = FastAPI(title="Morya Mens Parlour + Sam")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/admin/login")


class Admin(Base):
    __tablename__ = "admins"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)


class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    email = Column(String)
    phone = Column(String, nullable=True)
    subject = Column(String)
    message = Column(Text)
    status = Column(String, default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)


class Review(Base):
    __tablename__ = "reviews"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    email = Column(String, nullable=True)
    rating = Column(Integer)
    review = Column(Text)
    status = Column(String, default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _hash(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def _init_admin():
    db = SessionLocal()
    admin = db.query(Admin).filter(Admin.username == "admin").first()
    if admin is None:
        db.add(Admin(username="admin", hashed_password=_hash(ADMIN_PASSWORD)))
    elif os.environ.get("ADMIN_PASSWORD"):  # setting the env var also rotates the password
        admin.hashed_password = _hash(ADMIN_PASSWORD)
    db.commit()
    db.close()


_init_admin()


def current_admin(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    err = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials",
                        headers={"WWW-Authenticate": "Bearer"})
    try:
        username = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM]).get("sub")
    except JWTError:
        raise err
    user = db.query(Admin).filter(Admin.username == username).first() if username else None
    if user is None:
        raise err
    return user


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except BookingError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------- schemas ----------------
class LoginRequest(BaseModel):
    username: str
    password: str


class AppointmentCreate(BaseModel):
    name: str
    phone: str
    email: Optional[str] = None
    date: str   # 2026-11-10
    time: str   # 15:30
    service: str
    notes: Optional[str] = None


class MessageCreate(BaseModel):
    name: str
    email: str
    phone: Optional[str] = None
    subject: str
    message: str


class ReviewCreate(BaseModel):
    name: str
    email: Optional[str] = None
    rating: int
    review: str


class StatusUpdate(BaseModel):
    status: str


class WalkinRequest(BaseModel):
    service: str


# ---------------- admin login ----------------
@app.post("/api/admin/login")
def login(data: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(Admin).filter(Admin.username == data.username).first()
    if not user or not bcrypt.checkpw(data.password.encode(), user.hashed_password.encode()):
        raise HTTPException(status_code=401, detail="Incorrect username or password",
                            headers={"WWW-Authenticate": "Bearer"})
    exp = datetime.utcnow() + timedelta(minutes=TOKEN_MINUTES)
    return {"access_token": jwt.encode({"sub": user.username, "exp": exp}, SECRET_KEY, algorithm=ALGORITHM),
            "token_type": "bearer"}


# ---------------- Sam: public booking endpoints ----------------
@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/services")
def services():
    return booking.list_services()


@app.get("/api/availability")
def availability(day: date, service: str):
    return {"day": day.isoformat(), "service": service,
            "free_times": _guard(booking.check_availability, day, service)}


@app.get("/api/next-available")
def next_available(service: str):
    return {"service": service, "next": _guard(booking.next_available, service)}


@app.get("/api/wait")
def wait(service: str):
    """Minutes until a barber is free for this service (0 = right now)."""
    return {"service": service, "wait_minutes": _guard(booking.wait_minutes, service),
            "next_available": _guard(booking.next_available, service)}


@app.post("/api/appointments")
def create_appointment(appt: AppointmentCreate):
    try:
        start = datetime.fromisoformat(f"{appt.date}T{appt.time}")
    except ValueError:
        raise HTTPException(status_code=400, detail="Please choose a valid date and time.")
    b = _guard(booking.book, appt.name, appt.phone, appt.service, start,
               email=appt.email or "", notes=appt.notes or "")
    return {"message": "Appointment created successfully", "id": b["id"], "start": b["start"]}


# ---------------- Sam: voice agent tools (called by Vapi) ----------------
@app.post("/api/voice/tools")
async def voice_tools(request: Request):
    key = os.environ.get("SAM_TOOL_KEY")
    if not key:
        raise HTTPException(status_code=503, detail="Voice tools are not configured (SAM_TOOL_KEY is not set).")
    supplied = request.headers.get("x-sam-key") or request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(supplied.encode(), key.encode()):
        raise HTTPException(status_code=401, detail="Unauthorized")
    message = (await request.json()).get("message") or {}
    if message.get("type") != "tool-calls":
        return {"results": []}   # other Vapi events need no reply
    return tools.handle(message)


# ---------------- admin: bookings ----------------
def _view(b: dict) -> dict:
    s = datetime.fromisoformat(b["start"])
    return {"id": b["id"], "name": b["name"], "phone": b["phone"], "email": b.get("email") or "",
            "date": s.date().isoformat(), "time": s.strftime("%I:%M %p"), "service": b["service"].title(),
            "notes": b.get("notes") or "", "status": b["status"]}


@app.get("/api/appointments")
def read_appointments(_: Admin = Depends(current_admin)):
    return [_view(b) for b in booking.list_all()]


@app.put("/api/appointments/{appt_id}/status")
def update_appointment(appt_id: int, upd: StatusUpdate, _: Admin = Depends(current_admin)):
    _guard(booking.set_status, appt_id, upd.status)
    return {"message": "Status updated"}


@app.delete("/api/appointments/{appt_id}")
def delete_appointment(appt_id: int, _: Admin = Depends(current_admin)):
    _guard(booking.delete, appt_id)
    return {"message": "Deleted successfully"}


@app.post("/api/walkin")
def walkin(req: WalkinRequest, _: Admin = Depends(current_admin)):
    """Tap when a walk-in sits down so wait times stay accurate."""
    return _guard(booking.add_walkin, req.service)


@app.get("/api/stats")
def stats(db: Session = Depends(get_db), _: Admin = Depends(current_admin)):
    rows = booking.list_all()
    return {"totalAppointments": len(rows),
            "pendingAppointments": sum(1 for r in rows if r["status"] == "pending"),
            "totalReviews": db.query(Review).count(), "totalBookings": db.query(Message).count()}


# ---------------- admin: messages and reviews (same as before) ----------------
def _crud(model, path, create_schema, public_create_msg):
    @app.post(f"/api/{path}")
    def create(item: create_schema, db: Session = Depends(get_db)):
        db.add(model(**item.dict()))
        db.commit()
        return {"message": public_create_msg}

    @app.get(f"/api/{path}")
    def read(db: Session = Depends(get_db), _: Admin = Depends(current_admin)):
        return db.query(model).order_by(model.created_at.desc()).all()

    @app.put(f"/api/{path}/{{item_id}}/status")
    def update(item_id: int, upd: StatusUpdate, db: Session = Depends(get_db), _: Admin = Depends(current_admin)):
        row = db.query(model).filter(model.id == item_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        row.status = upd.status
        db.commit()
        return {"message": "Status updated"}

    @app.delete(f"/api/{path}/{{item_id}}")
    def remove(item_id: int, db: Session = Depends(get_db), _: Admin = Depends(current_admin)):
        row = db.query(model).filter(model.id == item_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        db.delete(row)
        db.commit()
        return {"message": "Deleted successfully"}


_crud(Message, "messages", MessageCreate, "Message created successfully")
_crud(Review, "reviews", ReviewCreate, "Review created successfully")

# ---------------- the website itself (must stay last) ----------------
if (ROOT / "site").exists():
    app.mount("/", StaticFiles(directory=ROOT / "site", html=True), name="site")
