"""Admin route smoke tests — requires CONNECT_TO_DB=false (mocked db)."""
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


def test_list_templates_empty(client):
    c, db = client
    db.table.return_value.select.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))
    resp = c.get("/admin/templates")
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_template(client):
    c, db = client
    mock_row = {
        "id": "t1", "title": "Test", "description": None, "track": None,
        "admin_prompt": None, "tool_config": {"mcq": True, "diagram": True, "voice": True, "camera": True, "coding": True},
        "is_published": False, "public_link_token": None, "created_at": "2024-01-01", "updated_at": "2024-01-01",
    }
    db.table.return_value.insert.return_value.execute = AsyncMock(return_value=MagicMock(data=[mock_row]))
    db.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(return_value=MagicMock(data=[{**mock_row, "workflow_steps": []}]))
    resp = c.post("/admin/templates", json={"title": "Test", "tool_config": {"mcq": True, "diagram": True, "voice": True, "camera": True, "coding": True}})
    assert resp.status_code == 200
    assert resp.json()["title"] == "Test"


def test_publish_template(client):
    c, db = client
    base = {
        "id": "t1", "title": "Test", "description": None, "track": None,
        "admin_prompt": None, "tool_config": {"mcq": True, "diagram": True, "voice": True, "camera": True, "coding": True},
        "is_published": True, "public_link_token": "abc123", "created_at": "2024-01-01", "updated_at": "2024-01-01",
    }
    db.table.return_value.select.return_value.eq.return_value.single.return_value.execute = AsyncMock(return_value=MagicMock(data=base))
    db.table.return_value.update.return_value.eq.return_value.execute = AsyncMock(return_value=MagicMock(data=[{**base, "public_link_token": "abc123"}]))
    resp = c.post("/admin/templates/t1/publish")
    assert resp.status_code == 200
    assert "public_link_token" in resp.json()
