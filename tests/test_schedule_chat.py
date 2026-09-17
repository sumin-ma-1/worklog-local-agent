from pathlib import Path

import pytest

from worklog_agent.config import AppConfig, StorageConfig
from worklog_agent.schedules import load_schedules
from worklog_agent.web.journal_ask import answer_journal_question


@pytest.mark.asyncio
async def test_schedule_chat_create_toggle_delete(tmp_path: Path) -> None:
    root = tmp_path / "user"
    root.mkdir()
    cfg = AppConfig(storage=StorageConfig(root=str(root)))

    created = await answer_journal_question(
        cfg, "매일 9시 어제 일지 예약 추가해줘", remember=False
    )
    assert created["intent"] == "schedule"
    assert "추가" in created["answer"]
    items = load_schedules(root)
    assert len(items) == 1
    assert items[0]["time"] == "09:00"
    assert items[0]["enabled"] is True

    disabled = await answer_journal_question(cfg, "어제 09:00 예약 꺼줘", remember=False)
    assert disabled["intent"] == "schedule"
    assert "비활성" in disabled["answer"]
    assert load_schedules(root)[0]["enabled"] is False

    deleted = await answer_journal_question(cfg, "어제 09:00 예약 삭제해줘", remember=False)
    assert deleted["intent"] == "schedule"
    assert "삭제" in deleted["answer"]
    assert load_schedules(root) == []
