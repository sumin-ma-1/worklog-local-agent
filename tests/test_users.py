from pathlib import Path

from worklog_agent.users import (
    create_share_token,
    find_share,
    has_legacy_layout,
    load_user_chats,
    migrate_legacy_to_user,
    save_user_chats,
    user_root,
)


def test_migrate_legacy_layout(tmp_path: Path) -> None:
    data = tmp_path / "data"
    (data / "journals").mkdir(parents=True)
    (data / "journals" / "2026-01-02.md").write_text("hello", encoding="utf-8")
    (data / "sessions").mkdir()
    (data / "sessions" / "worklog.session").write_bytes(b"sess")
    assert has_legacy_layout(data) is True
    assert migrate_legacy_to_user(data, 42, chats=[-1001]) is True
    dest = user_root(data, 42)
    assert (dest / "journals" / "2026-01-02.md").read_text(encoding="utf-8") == "hello"
    assert load_user_chats(dest) == [-1001]
    assert has_legacy_layout(data) is False


def test_share_token_roundtrip(tmp_path: Path) -> None:
    users = tmp_path / "users" / "9"
    users.mkdir(parents=True)
    meta = create_share_token(users, "2026-03-05")
    found = find_share(tmp_path, meta["token"])
    assert found is not None
    assert found[1]["day"] == "2026-03-05"
