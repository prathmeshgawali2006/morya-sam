import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/morya.db"   # must be set before the app is imported
os.environ["SECRET_KEY"] = "test-secret"
os.environ["ADMIN_PASSWORD"] = "test-pass"
os.environ["SAM_TOOL_KEY"] = "tool-key"

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_bookings_db(tmp_path, monkeypatch):
    monkeypatch.setenv("SAM_DB", str(tmp_path / "sam.db"))
