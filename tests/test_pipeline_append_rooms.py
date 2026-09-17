from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from worklog_agent.config import AppConfig, StorageConfig
from worklog_agent.models import DailyBundle, DailyChat, MessageRecord
from worklog_agent.pipeline import Pipeline
from worklog_agent.storage import Storage


def _config_for(root: Path) -> AppConfig:
    return AppConfig(storage=StorageConfig(root=str(root)))


def _bundle_with_two_rooms() -> DailyBundle:
    return DailyBundle(
        date="2026-05-01",
        timezone="Asia/Seoul",
        chats=[
            DailyChat(
                chat_id=-1001,
                title="room-a",
                message_count=1,
                messages=[
                    MessageRecord(
                        id=1,
                        chat_id=-1001,
                        chat_title="room-a",
                        date=datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc),
                        text="a",
                    )
                ],
            ),
            DailyChat(
                chat_id=-1002,
                title="room-b",
                message_count=1,
                messages=[
                    MessageRecord(
                        id=2,
                        chat_id=-1002,
                        chat_title="room-b",
                        date=datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc),
                        text="b",
                    )
                ],
            ),
        ],
        totals={"chats": 2, "messages": 2, "attachments": 0},
    )


@pytest.mark.asyncio
async def test_journal_append_rooms_keeps_existing(tmp_path: Path) -> None:
    root = tmp_path / "data"
    storage = Storage(root)
    storage.ensure()
    storage.save_room_journal("2026-05-01", -1001, "# keep me\n")
    storage.write_room_journals_index(
        "2026-05-01",
        [{"id": "-1001", "title": "room-a"}],
    )
    storage.save_daily(_bundle_with_two_rooms())

    config = _config_for(root)
    pipeline = Pipeline(config)

    async def fake_generate(bundle, _config, *, model=None, scope="combined"):
        if scope == "room":
            chat = bundle.chats[0]
            return f"# generated {chat.chat_id}\n"
        return "# combined\n"

    with (
        patch.object(pipeline, "organize", return_value=_bundle_with_two_rooms()),
        patch("worklog_agent.pipeline.list_running_model_names", new=AsyncMock(return_value=[])),
        patch("worklog_agent.pipeline.warmup_model", new=AsyncMock(return_value="test-model")),
        patch("worklog_agent.pipeline.generate_journal", new=AsyncMock(side_effect=fake_generate)),
        patch("worklog_agent.pipeline.day_source_fingerprint", return_value="fp"),
    ):
        await pipeline.journal(
            "2026-05-01",
            generate_type="per_room",
            append_rooms=True,
        )

    kept = storage.room_journal_path("2026-05-01", -1001).read_text(encoding="utf-8")
    added = storage.room_journal_path("2026-05-01", -1002).read_text(encoding="utf-8")
    assert kept == "# keep me\n"
    assert "generated -1002" in added
    rooms = storage.list_room_journals("2026-05-01")
    assert {row["id"] for row in rooms} == {"-1001", "-1002"}


@pytest.mark.asyncio
async def test_journal_both_append_regenerates_combined(tmp_path: Path) -> None:
    root = tmp_path / "data"
    storage = Storage(root)
    storage.ensure()
    storage.save_journal("2026-05-01", "# old combined\n")
    storage.save_room_journal("2026-05-01", -1001, "# keep me\n")
    storage.write_room_journals_index(
        "2026-05-01",
        [{"id": "-1001", "title": "room-a"}],
    )
    storage.save_daily(_bundle_with_two_rooms())

    config = _config_for(root)
    pipeline = Pipeline(config)

    async def fake_generate(bundle, _config, *, model=None, scope="combined"):
        if scope == "room":
            return f"# generated {bundle.chats[0].chat_id}\n"
        return "# new combined\n"

    with (
        patch.object(pipeline, "organize", return_value=_bundle_with_two_rooms()),
        patch("worklog_agent.pipeline.list_running_model_names", new=AsyncMock(return_value=[])),
        patch("worklog_agent.pipeline.warmup_model", new=AsyncMock(return_value="test-model")),
        patch("worklog_agent.pipeline.generate_journal", new=AsyncMock(side_effect=fake_generate)),
        patch("worklog_agent.pipeline.day_source_fingerprint", return_value="fp"),
    ):
        await pipeline.journal(
            "2026-05-01",
            generate_type="both",
            append_rooms=True,
        )

    assert storage.journal_path("2026-05-01").read_text(encoding="utf-8") == "# new combined\n"
    assert storage.room_journal_path("2026-05-01", -1001).read_text(encoding="utf-8") == "# keep me\n"
    assert "generated -1002" in storage.room_journal_path("2026-05-01", -1002).read_text(
        encoding="utf-8"
    )
