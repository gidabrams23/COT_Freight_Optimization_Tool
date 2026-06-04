import os

os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")

import app as app_module


def _set_base_session(client):
    with client.session_transaction() as session_state:
        session_state[app_module.SESSION_PROFILE_ID_KEY] = 1
        session_state[app_module.SESSION_PROFILE_NAME_KEY] = "Planner A"
        session_state[app_module.SESSION_PROFILE_SANDBOX_KEY] = False
        session_state["role"] = app_module.ROLE_PLANNER
        session_state["allowed_plants"] = ["GA"]
        session_state[app_module.SESSION_ALLOWED_PROFILE_IDS_KEY] = [1]


def test_access_switch_rejects_profile_not_in_allowed_ids(monkeypatch):
    client = app_module.app.test_client()
    _set_base_session(client)

    monkeypatch.setattr(
        app_module.db,
        "get_access_profile",
        lambda profile_id: {
            "id": 1,
            "name": "Planner A",
            "is_admin": 0,
            "is_sandbox": 0,
            "allowed_plants": "ALL",
            "default_plants": "ALL",
        }
        if profile_id == 1
        else {
            "id": 2,
            "name": "Admin",
            "is_admin": 1,
            "is_sandbox": 0,
            "allowed_plants": "ALL",
            "default_plants": "ALL",
        },
    )

    response = client.post("/access/switch", data={"profile_id": "2"}, follow_redirects=False)
    assert response.status_code in {301, 302}
    assert response.headers["Location"].endswith("/orders")

    with client.session_transaction() as session_state:
        assert session_state.get(app_module.SESSION_PROFILE_ID_KEY) == 1


def test_auth_microsoft_callback_maps_email_to_profile(monkeypatch):
    client = app_module.app.test_client()
    monkeypatch.setattr(app_module, "ENTRA_SSO_ACTIVE", True)
    monkeypatch.setattr(app_module, "ENTRA_SSO_REQUIRED", True)
    monkeypatch.setattr(app_module, "ENTRA_ALLOWED_EMAIL_DOMAINS", set())

    class _FakeMsalClient:
        def acquire_token_by_authorization_code(self, **_kwargs):
            return {
                "id_token_claims": {
                    "preferred_username": "planner@example.com",
                    "name": "Planner User",
                }
            }

    monkeypatch.setattr(app_module, "_build_entra_client", lambda: _FakeMsalClient())
    monkeypatch.setattr(
        app_module.db,
        "get_access_profile_for_identity",
        lambda email, provider=None: {
            "id": 7,
            "name": "Planner 7",
            "is_admin": 0,
            "is_sandbox": 0,
            "allowed_plants": "ALL",
            "default_plants": "ALL",
        }
        if email == "planner@example.com"
        else None,
    )

    with client.session_transaction() as session_state:
        session_state[app_module.SESSION_ENTRA_OAUTH_STATE_KEY] = "state-123"
        session_state[app_module.SESSION_ENTRA_OAUTH_NEXT_KEY] = "/orders"

    response = client.get(
        "/auth/microsoft/callback?state=state-123&code=abc123",
        follow_redirects=False,
    )
    assert response.status_code in {301, 302}
    assert "/login" in response.headers.get("Location", "")

    with client.session_transaction() as session_state:
        assert session_state.get(app_module.SESSION_PROFILE_ID_KEY) is None
        assert session_state.get(app_module.SESSION_SSO_EMAIL_KEY) == "planner@example.com"
        assert session_state.get(app_module.SESSION_ALLOWED_PROFILE_IDS_KEY) == [7]


def test_auth_microsoft_callback_rejects_unmapped_email(monkeypatch):
    client = app_module.app.test_client()
    monkeypatch.setattr(app_module, "ENTRA_SSO_ACTIVE", True)
    monkeypatch.setattr(app_module, "ENTRA_ALLOWED_EMAIL_DOMAINS", set())

    class _FakeMsalClient:
        def acquire_token_by_authorization_code(self, **_kwargs):
            return {"id_token_claims": {"preferred_username": "missing@example.com"}}

    monkeypatch.setattr(app_module, "_build_entra_client", lambda: _FakeMsalClient())
    monkeypatch.setattr(
        app_module.db,
        "get_access_profile_for_identity",
        lambda *_args, **_kwargs: None,
    )

    with client.session_transaction() as session_state:
        session_state[app_module.SESSION_ENTRA_OAUTH_STATE_KEY] = "state-xyz"
        session_state[app_module.SESSION_ENTRA_OAUTH_NEXT_KEY] = "/orders"

    response = client.get(
        "/auth/microsoft/callback?state=state-xyz&code=abc123",
        follow_redirects=False,
    )
    assert response.status_code in {301, 302}
    assert "/login" in response.headers.get("Location", "")


def test_login_hides_account_selection_until_sso(monkeypatch):
    client = app_module.app.test_client()
    monkeypatch.setattr(app_module, "ENTRA_SSO_ACTIVE", True)
    monkeypatch.setattr(app_module, "ENTRA_SSO_REQUIRED", False)

    response = client.get("/login")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Continue with Microsoft above to access system accounts." in body
    assert "Select Account" not in body


def test_login_allows_local_dev_auth_bypass_for_loopback(monkeypatch):
    client = app_module.app.test_client()
    monkeypatch.setattr(app_module, "ENTRA_SSO_ACTIVE", True)
    monkeypatch.setattr(app_module, "ENTRA_SSO_REQUIRED", True)
    monkeypatch.setattr(app_module, "LOCAL_DEV_AUTH_BYPASS_ENABLED", True)
    monkeypatch.setattr(app_module, "_is_local_dev_mode", lambda: True)
    monkeypatch.setattr(
        app_module.db,
        "list_access_profiles",
        lambda: [
            {
                "id": 1,
                "name": "Planner A",
                "is_admin": 0,
                "is_sandbox": 0,
                "allowed_plants": "ALL",
                "default_plants": "ALL",
            }
        ],
    )
    monkeypatch.setattr(
        app_module.db,
        "get_access_profile",
        lambda profile_id: {
            "id": 1,
            "name": "Planner A",
            "is_admin": 0,
            "is_sandbox": 0,
            "allowed_plants": "ALL",
            "default_plants": "ALL",
        }
        if profile_id == 1
        else None,
    )
    monkeypatch.setattr(app_module, "_set_order_refresh_prompt_if_outdated", lambda: None)
    monkeypatch.setattr(app_module, "_set_sql_auto_refresh_notice_for_first_login", lambda: None)

    response = client.get(
        "/login",
        base_url="http://127.0.0.1:5000",
        environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Local development auth bypass is active" in body
    assert "Select Account" in body

    login_response = client.post(
        "/login",
        data={"profile_id": "1"},
        base_url="http://127.0.0.1:5000",
        environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
        follow_redirects=False,
    )
    assert login_response.status_code in {301, 302}
    assert "/login" not in login_response.headers.get("Location", "")

    with client.session_transaction() as session_state:
        assert session_state.get(app_module.SESSION_PROFILE_ID_KEY) == 1
        assert session_state.get(app_module.SESSION_SSO_EMAIL_KEY) is None


def test_auth_microsoft_start_redirects_back_to_login_when_local_bypass_active(monkeypatch):
    client = app_module.app.test_client()
    monkeypatch.setattr(app_module, "LOCAL_DEV_AUTH_BYPASS_ENABLED", True)
    monkeypatch.setattr(app_module, "_is_local_dev_mode", lambda: True)

    response = client.get(
        "/auth/microsoft/start?next=/upload",
        base_url="http://127.0.0.1:5000",
        environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
        follow_redirects=False,
    )
    assert response.status_code in {301, 302}
    assert "/login?next=/upload" in response.headers.get("Location", "")
