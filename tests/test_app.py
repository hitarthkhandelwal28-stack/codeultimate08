import sqlite3
import pytest

import app as app_module


@pytest.fixture()
def client(tmp_path, monkeypatch):
    database = tmp_path / "test.sqlite3"
    monkeypatch.setattr(app_module, "DB_PATH", database)
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(app_module, "WORKSPACE_DIR", tmp_path / "workspaces")
    app_module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
    app_module.init_db()
    with app_module.app.test_client() as test_client:
        yield test_client


def test_health_check(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_portfolio_page_links_to_live_codeai(client):
    response = client.get("/portfolio")
    assert response.status_code == 200
    assert b"https://codeultimate08.onrender.com" in response.data


def test_contact_form_saves_valid_message(client):
    response = client.post("/api/contact", json={
        "name": "Test User",
        "email": "test@example.com",
        "message": "I would like to discuss a project."
    })
    assert response.status_code == 201
    assert "received" in response.get_json()["message"].lower()


@pytest.mark.parametrize("payload", [
    {"name": "", "email": "test@example.com", "message": "Hello"},
    {"name": "Test", "email": "not-an-email", "message": "Hello"},
    {"name": "Test", "email": "test@example.com", "message": ""},
    {"name": "x" * 81, "email": "test@example.com", "message": "Hello"},
])
def test_contact_form_rejects_invalid_data(client, payload):
    response = client.post("/api/contact", json=payload)
    assert response.status_code == 400


def test_contact_form_rejects_cross_origin(client):
    response = client.post(
        "/api/contact",
        json={"name": "Test", "email": "test@example.com", "message": "Hello"},
        headers={"Origin": "https://evil.example"}
    )
    assert response.status_code == 403


def test_contact_honeypot_does_not_store_message(client, tmp_path):
    response = client.post("/api/contact", json={
        "name": "Bot", "email": "bot@example.com",
        "message": "spam", "website": "https://spam.example"
    })
    assert response.status_code == 202
    with app_module.db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM contact_messages").fetchone()[0]
    assert count == 0


def test_create_chat_and_validate_message(client, monkeypatch):
    created = client.post("/api/chats")
    assert created.status_code == 201
    chat_id = created.get_json()["id"]

    empty = client.post("/api/chat", json={"chat_id": chat_id, "message": "  "})
    assert empty.status_code == 400

    bad_id = client.get("/api/chats/not-a-valid-id/messages")
    assert bad_id.status_code == 404


def test_security_headers_are_present(client):
    response = client.get("/healthz")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
