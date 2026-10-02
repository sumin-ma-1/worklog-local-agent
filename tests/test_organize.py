from datetime import datetime, timezone
from pathlib import Path

from worklog_agent.models import MediaRef, MessageRecord
from worklog_agent.organize import build_daily_bundle, to_local_date
from worklog_agent.storage import Storage, slugify


def _msg(**kwargs) -> MessageRecord:
    defaults = {
        "id": 1,
        "chat_id": -1001,
        "chat_title": "팀 업무방",
        "date": datetime(2026, 9, 2, 15, 30, tzinfo=timezone.utc),
        "sender_id": 10,
        "sender_name": "김수민",
        "text": "배포 완료",
    }
    defaults.update(kwargs)
    return MessageRecord(**defaults)


def test_kst_date_rolls_at_utc_midnight() -> None:
    dt = datetime(2026, 9, 2, 15, 30, tzinfo=timezone.utc)
    assert to_local_date(dt, "Asia/Seoul").isoformat() == "2026-09-03"


def test_build_daily_bundle_groups_by_local_date() -> None:
    messages = [
        _msg(id=1, date=datetime(2026, 9, 2, 14, 59, tzinfo=timezone.utc), text="어제"),
        _msg(
            id=2,
            date=datetime(2026, 9, 2, 15, 1, tzinfo=timezone.utc),
            text="오늘",
            media=MediaRef(type="document", file_name="spec.pdf"),
        ),
        _msg(
            id=3,
            chat_id=-1002,
            chat_title="디자인",
            date=datetime(2026, 9, 2, 16, 0, tzinfo=timezone.utc),
            sender_name="이디자인",
            text="시안 공유",
        ),
    ]
    bundle = build_daily_bundle(messages, "2026-09-03", "Asia/Seoul")
    assert bundle.totals["messages"] == 2
    assert bundle.totals["chats"] == 2
    assert bundle.totals["attachments"] == 1
    titles = {chat.title for chat in bundle.chats}
    assert titles == {"팀 업무방", "디자인"}


def test_build_daily_bundle_filters_watched_chats() -> None:
    messages = [
        _msg(id=1, date=datetime(2026, 9, 2, 15, 1, tzinfo=timezone.utc), text="오늘"),
        _msg(
            id=2,
            chat_id=-1002,
            chat_title="디자인",
            date=datetime(2026, 9, 2, 16, 0, tzinfo=timezone.utc),
            text="시안",
        ),
    ]
    bundle = build_daily_bundle(messages, "2026-09-03", "Asia/Seoul", chat_ids={-1001})
    assert bundle.totals["chats"] == 1
    assert bundle.chats[0].title == "팀 업무방"


def test_storage_roundtrip(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    storage.ensure()
    record = _msg()
    storage.append_messages(record.chat_id, [record])
    loaded = storage.read_messages(record.chat_id)
    assert loaded[0].text == "배포 완료"
    assert slugify("팀 업무방!") == "팀_업무방"
