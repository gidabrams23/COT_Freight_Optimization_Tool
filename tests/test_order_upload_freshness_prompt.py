import os

import db

os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")

import app as app_module


def _set_authenticated_session(client):
    profiles = db.list_access_profiles()
    assert profiles
    with client.session_transaction() as session_state:
        session_state[app_module.SESSION_PROFILE_ID_KEY] = profiles[0]["id"]


def _first_non_admin_profile_id():
    for profile in db.list_access_profiles():
        if not bool(profile.get("is_admin")):
            return profile.get("id")
    raise AssertionError("Expected at least one non-admin access profile")


def test_build_order_upload_freshness_marks_outdated_when_last_upload_not_today():
    freshness = app_module._build_order_upload_freshness(
        {"uploaded_at": "2026-03-19T13:00:00"},
        today=app_module.date(2026, 3, 20),
    )

    assert freshness["is_outdated"] is True
    assert freshness["status_label"] == "Outdated"


def test_login_sets_refresh_prompt_flag_when_orders_are_outdated(monkeypatch):
    client = app_module.app.test_client()
    monkeypatch.setattr(app_module, "ENTRA_SSO_ACTIVE", False)
    monkeypatch.setattr(
        app_module.db,
        "get_last_upload",
        lambda: {"uploaded_at": "2026-03-19T11:00:00"},
    )

    response = client.post(
        "/login",
        data={"profile_id": str(_first_non_admin_profile_id())},
        follow_redirects=False,
    )

    assert response.status_code in {301, 302}
    assert "/orders" in response.headers.get("Location", "")
    with client.session_transaction() as session_state:
        assert session_state.get(app_module.SESSION_ORDER_REFRESH_PROMPT_KEY) is True


def test_orders_route_auto_opens_refresh_guide_when_prompt_flag_present(monkeypatch):
    client = app_module.app.test_client()
    _set_authenticated_session(client)
    monkeypatch.setattr(
        app_module.db,
        "get_last_upload",
        lambda: {"uploaded_at": "2026-03-19T11:00:00"},
    )
    with client.session_transaction() as session_state:
        session_state[app_module.SESSION_ORDER_REFRESH_PROMPT_KEY] = True

    response = client.get("/orders")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'data-refresh-guide-modal' in body
    assert 'data-auto-open="true"' in body
    assert "Open order report outdated: please refresh data and upload to tool." in body
