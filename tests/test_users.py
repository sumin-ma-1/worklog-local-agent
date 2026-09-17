from pathlib import Path

from worklog_agent.accounts import AccountError, AccountStore, is_admin_username
from worklog_agent.users import (
    create_share_token,
    delete_user_data,
    find_share,
    has_legacy_layout,
    load_user_chats,
    load_user_telegram,
    migrate_legacy_to_user,
    save_user_telegram,
    telegram_linked,
    user_root,
)


def test_account_register_and_login(tmp_path: Path) -> None:
    store = AccountStore(tmp_path)
    user = store.register("Alice", "password1")
    assert user["username"] == "alice"
    authed = store.authenticate("alice", "password1")
    assert authed["id"] == user["id"]
    try:
        store.authenticate("alice", "wrong-pass")
        assert False, "expected failure"
    except AccountError:
        pass


def test_account_list_delete_and_admin_flag(tmp_path: Path) -> None:
    store = AccountStore(tmp_path)
    a = store.register("alice", "password1")
    store.register("bob", "password1")
    assert is_admin_username("devsm") is True
    assert is_admin_username("alice") is False
    public = store.list_public()
    assert {u["username"] for u in public} == {"alice", "bob"}
    assert all("password_hash" not in u for u in public)
    root = user_root(tmp_path, a["id"])
    root.mkdir(parents=True)
    (root / "marker.txt").write_text("x", encoding="utf-8")
    assert store.delete(a["id"]) is True
    assert store.get(a["id"]) is None
    assert delete_user_data(tmp_path, a["id"]) is True
    assert not root.exists()


def test_migrate_legacy_layout(tmp_path: Path) -> None:
    data = tmp_path / "data"
    (data / "journals").mkdir(parents=True)
    (data / "journals" / "2026-01-02.md").write_text("hello", encoding="utf-8")
    (data / "sessions").mkdir()
    (data / "sessions" / "worklog.session").write_bytes(b"sess")
    assert has_legacy_layout(data) is True
    assert migrate_legacy_to_user(data, "local-1", chats=[-1001]) is True
    dest = user_root(data, "local-1")
    assert (dest / "journals" / "2026-01-02.md").read_text(encoding="utf-8") == "hello"
    assert load_user_chats(dest) == [-1001]
    assert telegram_linked(dest) is True
    assert has_legacy_layout(data) is False


def test_user_telegram_and_share(tmp_path: Path) -> None:
    users = tmp_path / "users" / "u1"
    users.mkdir(parents=True)
    save_user_telegram(users, {"phone": "+821011122233"})
    assert load_user_telegram(users)["phone"] == "+821011122233"
    meta = create_share_token(users, "2026-03-05")
    found = find_share(tmp_path, meta["token"])
    assert found is not None
    assert found[1]["day"] == "2026-03-05"
    again = create_share_token(users, "2026-03-05")
    assert again["token"] == meta["token"]
    assert find_share(tmp_path, meta["token"]) is not None


def test_library_share_reuses_token(tmp_path: Path) -> None:
    from worklog_agent.users import create_library_share_token

    users = tmp_path / "users" / "u1"
    users.mkdir(parents=True)
    (users / "journals").mkdir()
    (users / "journals" / "2026-03-05.md").write_text("# hi\n", encoding="utf-8")
    first = create_library_share_token(users, mode="live")
    second = create_library_share_token(users, mode="live")
    assert first["token"] == second["token"]
    assert find_share(tmp_path, first["token"]) is not None
    snap_a = create_library_share_token(users, mode="snapshot")
    snap_b = create_library_share_token(users, mode="snapshot")
    assert snap_a["token"] == snap_b["token"]
    assert snap_a["token"] != first["token"]
