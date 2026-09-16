from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from worklog_agent.users import ensure_user_root, user_root
from worklog_agent.web.app import create_app
from worklog_agent.web.auth_session import COOKIE_USER


def _write_config(path: Path, root: Path) -> Path:
    path.write_text(
        "\n".join(
            [
                "timezone: Asia/Seoul",
                "telegram:",
                "  session_name: worklog",
                "  chats: []",
                "storage:",
                f"  root: {root}",
                "collect:",
                "  lookback_days: 7",
                "  skip_service_messages: true",
                "journal:",
                "  language: ko",
                "  temperature: 0.2",
                "  ollama:",
                "    host: http://127.0.0.1:11434",
                "    model: gemma4:e4b",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _write_env(path: Path) -> None:
    path.write_text(
        "TELEGRAM_API_ID=111\nTELEGRAM_API_HASH=hashhashhash\n",
        encoding="utf-8",
    )


def _register(client: TestClient, username: str = "alice", password: str = "password1") -> dict:
    response = client.post("/api/auth/register", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def _link_session(state, user_id: str) -> None:
    root = ensure_user_root(state.config.data_root, user_id)
    sessions = root / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / "worklog.session").write_bytes(b"test-session")


def _authed_linked_client(tmp_path: Path, *, username: str = "alice"):
    config_path = _write_config(tmp_path / "config.yaml", tmp_path / "data")
    _write_env(tmp_path / ".env")
    app = create_app(config_path)
    client = TestClient(app)
    data = _register(client, username=username)
    user_id = data["user"]["id"]
    _link_session(app.state.dashboard, user_id)
    return client, app.state.dashboard, user_id


def test_home_has_account_login(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path / "config.yaml", tmp_path / "data")
    client = TestClient(create_app(config_path))
    home = client.get("/")
    assert home.status_code == 200
    assert "가입하기" in home.text
    assert 'id="goto-register"' in home.text
    assert "api-id-input" not in home.text
    assert "password-confirm-input" in home.text
    assert "login-phone-input" in home.text
    assert 'data-view="accounts"' in home.text
    assert 'id="view-accounts"' in home.text


def test_register_login_and_telegram_required(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path / "config.yaml", tmp_path / "data")
    _write_env(tmp_path / ".env")
    client = TestClient(create_app(config_path))
    registered = client.post(
        "/api/auth/register", json={"username": "bob", "password": "password1"}
    )
    assert registered.status_code == 200
    assert registered.json()["user"]["username"] == "bob"
    assert registered.json()["telegram"]["linked"] is False

    assert client.get("/api/journals").status_code == 403
    assert client.get("/api/journals").json()["detail"] == "telegram_required"

    me = client.get("/api/me").json()
    assert me["user"]["username"] == "bob"
    assert me["telegram"]["linked"] is False
    assert me["telegram"]["api_ready"] is True


def test_api_requires_login(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path / "config.yaml", tmp_path / "data")
    client = TestClient(create_app(config_path))
    assert client.get("/api/journals").status_code == 401
    assert client.get("/api/chats").status_code == 401
    assert client.put("/api/journals/2026-07-28", json={"markdown": "x"}).status_code == 401


def test_dashboard_add_and_delete_chat(tmp_path: Path) -> None:
    client, _, _ = _authed_linked_client(tmp_path)
    added = client.post("/api/chats", json={"id": "-100111", "title": "운영팀"})
    assert added.status_code == 200
    assert added.json()["chats"][0]["id"] == -100111
    removed = client.post("/api/chats/delete", json={"id": -100111})
    assert removed.status_code == 200
    assert removed.json()["chats"] == []


def test_dialogs_mark_watched(tmp_path: Path) -> None:
    client, _, _ = _authed_linked_client(tmp_path)
    client.post("/api/chats", json={"id": -1001})
    fake = [{"id": -1001, "title": "팀 업무방", "type": "supergroup"}]
    with patch("worklog_agent.collect.load_dialogs", new=AsyncMock(return_value=fake)):
        data = client.get("/api/dialogs").json()
    assert data["dialogs"][0]["watched"] is True


def test_telegram_link_requires_server_api(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path / "config.yaml", tmp_path / "data")
    client = TestClient(create_app(config_path))
    _register(client, "carol")
    response = client.post("/api/telegram/login/start", json={"phone": "+821011122233"})
    assert response.status_code == 503
    assert "TELEGRAM_API" in response.json()["detail"]


def test_telegram_link_start_phone_only(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path / "config.yaml", tmp_path / "data")
    _write_env(tmp_path / ".env")
    client = TestClient(create_app(config_path))
    _register(client, "dana")
    with patch(
        "worklog_agent.web.app.start_login",
        new=AsyncMock(return_value={"stage": "code", "message": "코드 전송"}),
    ):
        response = client.post("/api/telegram/login/start", json={"phone": "+821011122233"})
    assert response.status_code == 200
    assert response.json()["stage"] == "code"


def test_auth_logout_keeps_telegram_session(tmp_path: Path) -> None:
    client, state, user_id = _authed_linked_client(tmp_path)
    session_file = user_root(state.config.data_root, user_id) / "sessions" / "worklog.session"
    assert session_file.is_file()
    response = client.post("/api/auth/logout")
    assert response.status_code == 200
    assert client.get("/api/journals").status_code == 401
    assert session_file.is_file()

    login = client.post("/api/auth/login", json={"username": "alice", "password": "password1"})
    assert login.status_code == 200
    assert login.json()["telegram"]["linked"] is True
    assert client.get("/api/journals").status_code == 200


def test_journal_update_and_delete(tmp_path: Path) -> None:
    client, state, user_id = _authed_linked_client(tmp_path)
    data_root = user_root(state.config.data_root, user_id)

    saved = client.put("/api/journals/2026-07-28", json={"markdown": "# 초안\n내용"})
    assert saved.status_code == 200
    assert (data_root / "journals" / "2026-07-28.md").is_file()

    deleted = client.delete("/api/journals/2026-07-28")
    assert deleted.status_code == 200
    assert not (data_root / "journals" / "2026-07-28.md").exists()


def test_users_are_isolated(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path / "config.yaml", tmp_path / "data")
    _write_env(tmp_path / ".env")
    app = create_app(config_path)
    a = TestClient(app)
    b = TestClient(app)
    ra = _register(a, "user_a")
    rb = _register(b, "user_b")
    _link_session(app.state.dashboard, ra["user"]["id"])
    _link_session(app.state.dashboard, rb["user"]["id"])

    assert a.put("/api/journals/2026-01-01", json={"markdown": "A only"}).status_code == 200
    assert b.put("/api/journals/2026-01-01", json={"markdown": "B only"}).status_code == 200
    assert a.get("/api/journals/2026-01-01").json()["markdown"].strip() == "A only"
    assert b.get("/api/journals/2026-01-01").json()["markdown"].strip() == "B only"


def test_share_link_is_public(tmp_path: Path) -> None:
    client, state, user_id = _authed_linked_client(tmp_path)
    client.put("/api/journals/2026-08-01", json={"markdown": "# 공유본"})
    shared = client.post("/api/journals/2026-08-01/share")
    assert shared.status_code == 200
    token = shared.json()["token"]

    guest = TestClient(create_app(state.config_path))
    page = guest.get(f"/s/{token}")
    assert page.status_code == 200
    assert "공유본" in guest.get(f"/api/share/{token}").json()["markdown"]
    assert guest.get("/api/journals/2026-08-01").status_code == 401
    assert (user_root(state.config.data_root, user_id) / "journals" / "2026-08-01.md").is_file()


def test_admin_users_api(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path / "config.yaml", tmp_path / "data")
    _write_env(tmp_path / ".env")
    app = create_app(config_path)
    admin = TestClient(app)
    other = TestClient(app)

    admin_data = _register(admin, "devsm", "password1")
    other_data = _register(other, "member1", "password1")
    _link_session(app.state.dashboard, admin_data["user"]["id"])
    _link_session(app.state.dashboard, other_data["user"]["id"])

    forbidden = other.get("/api/admin/users")
    assert forbidden.status_code == 403

    listed = admin.get("/api/admin/users")
    assert listed.status_code == 200
    users = listed.json()["users"]
    usernames = {item["username"] for item in users}
    assert usernames == {"devsm", "member1"}
    assert all("password_hash" not in item for item in users)
    member = next(item for item in users if item["username"] == "member1")
    assert member["telegram_linked"] is True
    assert member["journal_count"] == 0
    assert member["chat_count"] == 0
    assert member["last_seen"]

    overview = admin.get("/api/overview").json()
    assert overview["is_admin"] is True
    assert other.get("/api/overview").json()["is_admin"] is False

    assert other.put("/api/journals/2026-03-01", json={"markdown": "# m"}).status_code == 200
    assert other.post("/api/chats", json={"id": "-100111", "title": "운영팀"}).status_code == 200
    member_after = next(
        item for item in admin.get("/api/admin/users").json()["users"] if item["username"] == "member1"
    )
    assert member_after["journal_count"] == 1
    assert member_after["chat_count"] == 1
    assert member_after["last_seen"]
    self_delete = admin.delete(f"/api/admin/users/{admin_data['user']['id']}")
    assert self_delete.status_code == 400

    deleted = admin.delete(f"/api/admin/users/{other_data['user']['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["ok"] is True
    remaining = {item["username"] for item in admin.get("/api/admin/users").json()["users"]}
    assert remaining == {"devsm"}
    assert not user_root(app.state.dashboard.config.data_root, other_data["user"]["id"]).exists()
    assert other.get("/api/journals").status_code == 401
