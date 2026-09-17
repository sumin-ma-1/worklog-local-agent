from pathlib import Path

import pytest

from worklog_agent.config import AppConfig, StorageConfig
from worklog_agent.storage import Storage
from worklog_agent.users import add_user_chat, load_user_chats
from worklog_agent.web.journal_ask import answer_journal_question


@pytest.mark.asyncio
async def test_watch_chat_on_off_by_title(tmp_path: Path) -> None:
    root = tmp_path / "user"
    root.mkdir()
    storage = Storage(root)
    storage.ensure()
    storage.save_chat_title(-1001, "금형 과제방")
    storage.save_chat_title(-1002, "배포방")
    add_user_chat(root, -1001)

    cfg = AppConfig(storage=StorageConfig(root=str(root)))

    off = await answer_journal_question(cfg, "금형방 수집 꺼줘", remember=False)
    assert off["intent"] == "watch"
    assert "껐" in off["answer"]
    assert load_user_chats(root) == []

    on = await answer_journal_question(cfg, "수집방 배포 켜줘", remember=False)
    assert on["intent"] == "watch"
    assert "켰" in on["answer"]
    assert load_user_chats(root) == [-1002]

    all_off = await answer_journal_question(cfg, "수집방 전부 꺼줘", remember=False)
    assert all_off["intent"] == "watch"
    assert load_user_chats(root) == []
