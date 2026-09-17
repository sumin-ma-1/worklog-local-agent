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
    assert "alice의 공유 일지" in page.text
    assert " 공유" in page.text
    shared_payload = guest.get(f"/api/share/{token}").json()
    assert "공유본" in shared_payload["markdown"]
    assert shared_payload["shared_by"] == "alice"
    assert shared_payload["shared_at"]
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


def test_run_plan_skip_existing(tmp_path: Path) -> None:
    client, state, user_id = _authed_linked_client(tmp_path)
    data_root = user_root(state.config.data_root, user_id)
    journals = data_root / "journals"
    journals.mkdir(parents=True, exist_ok=True)
    (journals / "2026-03-01.md").write_text("# one", encoding="utf-8")
    (journals / "2026-03-02.md").write_text("# two", encoding="utf-8")

    response = client.post(
        "/api/run/plan",
        json={"start": "2026-03-01", "end": "2026-03-03", "skip_existing": True},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["run_count"] == 1
    assert data["skip_count"] == 2
    assert data["plan"][0]["action"] == "skip"
    assert data["plan"][-1]["action"] == "run"

    empty = client.post(
        "/api/run",
        json={"start": "2026-03-01", "end": "2026-03-02", "skip_existing": True},
    )
    assert empty.status_code == 400
    assert empty.json()["detail"] == "생성할 날짜가 없습니다."


def test_run_plan_range_limit(tmp_path: Path) -> None:
    client, _, _ = _authed_linked_client(tmp_path)
    response = client.post(
        "/api/run/plan",
        json={"start": "2026-01-01", "end": "2026-02-05"},
    )
    assert response.status_code == 400
    assert "31" in response.json()["detail"]


def test_run_plan_pick_dates(tmp_path: Path) -> None:
    client, _, _ = _authed_linked_client(tmp_path)
    response = client.post(
        "/api/run/plan",
        json={"dates": ["2026-03-01", "2026-03-05", "2026-03-03"]},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["dates"] == ["2026-03-05", "2026-03-03", "2026-03-01"]
    assert len(data["plan"]) == 3
    assert data["run_count"] == 3


def test_run_plan_regenerate_if_stale(tmp_path: Path) -> None:
    client, state, user_id = _authed_linked_client(tmp_path)
    data_root = user_root(state.config.data_root, user_id)
    journals = data_root / "journals"
    journals.mkdir(parents=True, exist_ok=True)
    (journals / "2026-03-01.md").write_text("# one", encoding="utf-8")
    (journals / "2026-03-01.meta.json").write_text(
        '{"date":"2026-03-01","source_fingerprint":"old"}',
        encoding="utf-8",
    )

    response = client.post(
        "/api/run/plan",
        json={"date": "2026-03-01", "regenerate_if_stale": True},
    )
    assert response.status_code == 200
    item = response.json()["plan"][0]
    assert item["action"] == "run"
    assert item["reason"] == "stale"


def test_schedules_api_crud(tmp_path: Path) -> None:
    client, state, user_id = _authed_linked_client(tmp_path)
    root = user_root(state.config.data_root, user_id)

    created = client.post(
        "/api/schedules",
        json={"name": "저녁 일지", "time": "21:00", "target": "yesterday", "skip_existing": True},
    )
    assert created.status_code == 200
    schedule_id = created.json()["schedule"]["id"]

    listed = client.get("/api/schedules")
    assert listed.status_code == 200
    assert len(listed.json()["schedules"]) == 1

    patched = client.patch(
        f"/api/schedules/{schedule_id}",
        json={"enabled": False, "name": "수정됨"},
    )
    assert patched.status_code == 200
    assert patched.json()["schedule"]["enabled"] is False
    assert patched.json()["schedule"]["name"] == "수정됨"

    deleted = client.delete(f"/api/schedules/{schedule_id}")
    assert deleted.status_code == 200


def test_journal_prompt_api(tmp_path: Path) -> None:
    from worklog_agent.journal import JOURNAL_SYSTEM_PROMPT
    from worklog_agent.journal_prompt import DEFAULT_SECTIONS
    from worklog_agent.users import user_config

    client, state, user_id = _authed_linked_client(tmp_path)
    cfg = user_config(state.config, user_id)

    defaulted = client.get("/api/journal-prompt")
    assert defaulted.status_code == 200
    assert defaulted.json()["is_default"] is True
    assert defaulted.json()["sections"] == DEFAULT_SECTIONS
    assert defaulted.json()["system"] == JOURNAL_SYSTEM_PROMPT

    custom = ["요약", "나만의 섹션"]
    saved = client.put("/api/journal-prompt", json={"sections": custom})
    assert saved.status_code == 200
    assert saved.json()["is_default"] is False
    assert saved.json()["sections"] == custom
    assert (cfg.data_root / "journal_prompt.json").is_file()

    empty = client.put("/api/journal-prompt", json={"sections": []})
    assert empty.status_code == 422

    duplicate = client.put("/api/journal-prompt", json={"sections": ["요약", "요약"]})
    assert duplicate.status_code == 400

    loaded = client.get("/api/journal-prompt")
    assert loaded.status_code == 200
    assert loaded.json()["sections"] == custom

    reset = client.post("/api/journal-prompt/reset")
    assert reset.status_code == 200
    assert reset.json()["is_default"] is True
    assert reset.json()["sections"] == DEFAULT_SECTIONS
    assert reset.json()["system"] == JOURNAL_SYSTEM_PROMPT
    assert not (cfg.data_root / "journal_prompt.json").exists()
