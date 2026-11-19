import os
import sqlite3


def connect() -> sqlite3.Connection:
    """Open the database (path from SAM_DB env var) and make sure the table exists."""
    path = os.environ.get("SAM_DB", "sam.db")
    conn = sqlite3.connect(path, isolation_level=None)  # we manage transactions ourselves
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            service TEXT NOT NULL,
            start TEXT NOT NULL,
            end TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'confirmed',
            email TEXT,
            notes TEXT
        )"""
    )
    conn.execute("CREATE TABLE IF NOT EXISTS tool_calls (id TEXT PRIMARY KEY, result TEXT NOT NULL)")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(bookings)")}
    for col in ("email", "notes"):
        if col not in cols:
            conn.execute(f"ALTER TABLE bookings ADD COLUMN {col} TEXT")
    return conn
