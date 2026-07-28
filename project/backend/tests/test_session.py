"""Session and chat route smoke tests."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    with patch("app.db.init_supabase"), patch("app.db.get_db") as mock_get_db:
        mock_db = AsyncMock()
        mock_get_db.return_value = mock_db
        from app.main import app
        return TestClient(app), mock_db


def test_start_session_invalid_token(client):
    c, db = client
    db.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))
    resp = c.post("/session/start", json={
        "token": "invalid", "candidate_name": "Ali", "candidate_email": "ali@test.com",
        "consent_camera": False, "consent_voice": False, "consent_data": True,
    })
    assert resp.status_code == 404


def test_start_session_ok(client):
    c, db = client
    template = {
        "id": "t1", "title": "Test Assessment", "track": "ai_ml", "is_published": True,
        "tool_config": {"mcq": True, "diagram": False, "voice": True, "camera": False, "coding": False},
    }
    session_row = {
        "id": "s1", "template_id": "t1", "candidate_name": "Ali", "candidate_email": "ali@test.com",
        "track": "ai_ml", "status": "in_progress", "agent_state": None,
        "consent_camera": False, "consent_voice": True, "consent_data": True,
        "created_at": "2024-01-01",
    }
    db.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=MagicMock(data=[template]))
    db.table.return_value.insert.return_value.execute = AsyncMock(return_value=MagicMock(data=[session_row]))
    resp = c.post("/session/start", json={
        "token": "valid-token", "candidate_name": "Ali", "candidate_email": "ali@test.com",
        "consent_camera": False, "consent_voice": True, "consent_data": True,
    })
    assert resp.status_code == 200
    assert resp.json()["session_id"] == "s1"


def test_health():
    from app.main import app
    c = TestClient(app)
    resp = c.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
