import os

import db

os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")

import app as app_module


def _set_authenticated_session(client):
    profiles = db.list_access_profiles()
    assert profiles
    with client.session_transaction() as session_state:
        session_state[app_module.SESSION_PROFILE_ID_KEY] = profiles[0]["id"]


def test_tutorial_route_redirects_without_session():
    client = app_module.app.test_client()
    response = client.get("/tutorial")

    assert response.status_code in {301, 302}
    assert "/login" in response.headers.get("Location", "")


def test_tutorial_route_returns_ok_for_authenticated_user():
    client = app_module.app.test_client()
    _set_authenticated_session(client)

    response = client.get("/tutorial")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Tutorial Center" in body
    assert "Upload Orders" in body


def test_tutorial_route_handles_missing_manifest(monkeypatch):
    client = app_module.app.test_client()
    _set_authenticated_session(client)
    monkeypatch.setattr(app_module, "TUTORIAL_MANIFEST_PATH", "Z:/definitely-missing/tutorial_manifest.json")

    response = client.get("/tutorial")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Tutorial content is unavailable right now." in body
