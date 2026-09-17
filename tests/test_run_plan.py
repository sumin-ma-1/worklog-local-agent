from datetime import datetime, timezone
from pathlib import Path

import pytest

from worklog_agent.journal_meta import write_journal_meta
from worklog_agent.models import MessageRecord
from worklog_agent.source_fingerprint import day_source_fingerprint
from worklog_agent.storage import Storage
from worklog_agent.web.run_plan import (
    dates_to_run,
    expand_date_range,
    plan_run_days,
    resolve_run_dates,
)


def _write_message(storage: Storage, *, msg_id: int, text: str) -> None:
    chat_id = -1001
    record = MessageRecord(
        id=msg_id,
        chat_id=chat_id,
        chat_title="team",
        date=datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc),
        text=text,
    )
    storage.write_messages(chat_id, [record])


def test_expand_date_range_inclusive() -> None:
    days = expand_date_range("2026-03-01", "2026-03-03")
    assert days == ["2026-03-01", "2026-03-02", "2026-03-03"]


def test_expand_date_range_swaps_when_reversed() -> None:
    days = expand_date_range("2026-03-05", "2026-03-03")
    assert days == ["2026-03-03", "2026-03-04", "2026-03-05"]


def test_expand_date_range_rejects_over_limit() -> None:
    with pytest.raises(ValueError, match="31"):
        expand_date_range("2026-01-01", "2026-02-05")


def test_resolve_run_dates_single_and_range() -> None:
    assert resolve_run_dates(date="2026-04-01") == ["2026-04-01"]
    assert resolve_run_dates(start="2026-04-01", end="2026-04-02") == [
        "2026-04-01",
        "2026-04-02",
    ]


def test_resolve_run_dates_requires_input() -> None:
    with pytest.raises(ValueError, match="날짜"):
        resolve_run_dates()


def test_resolve_run_dates_explicit_list() -> None:
    assert resolve_run_dates(dates=["2026-04-03", "2026-04-01", "2026-04-02"]) == [
        "2026-04-03",
        "2026-04-02",
        "2026-04-01",
    ]


def test_plan_run_days_skip_existing(tmp_path: Path) -> None:
    root = tmp_path / "data"
    storage = Storage(root)
    storage.journals.mkdir(parents=True, exist_ok=True)
    storage.journal_path("2026-05-01").write_text("# one", encoding="utf-8")

    plan = plan_run_days(
        storage,
        ["2026-05-01", "2026-05-02"],
        skip_existing=True,
    )
    assert [item["action"] for item in plan] == ["skip", "run"]
    assert dates_to_run(plan) == ["2026-05-02"]


def test_plan_run_days_force_overrides_skip(tmp_path: Path) -> None:
    root = tmp_path / "data"
    storage = Storage(root)
    storage.journals.mkdir(parents=True, exist_ok=True)
    storage.journal_path("2026-05-01").write_text("# one", encoding="utf-8")

    plan = plan_run_days(
        storage,
        ["2026-05-01"],
        skip_existing=True,
        force=True,
    )
    assert plan[0]["action"] == "run"
    assert plan[0]["reason"] == "force"


def test_plan_run_days_regenerate_if_stale_up_to_date(tmp_path: Path) -> None:
    root = tmp_path / "data"
    storage = Storage(root)
    storage.ensure()
    _write_message(storage, msg_id=1, text="hello")
    storage.journal_path("2026-05-01").write_text("# one", encoding="utf-8")
    fingerprint = day_source_fingerprint(storage, "2026-05-01", "Asia/Seoul")
    write_journal_meta(storage, "2026-05-01", source_fingerprint=fingerprint)

    plan = plan_run_days(
        storage,
        ["2026-05-01"],
        regenerate_if_stale=True,
        tz_name="Asia/Seoul",
    )
    assert plan[0]["action"] == "skip"
    assert plan[0]["reason"] == "up_to_date"


def test_plan_run_days_regenerate_if_stale_detects_change(tmp_path: Path) -> None:
    root = tmp_path / "data"
    storage = Storage(root)
    storage.ensure()
    _write_message(storage, msg_id=1, text="hello")
    storage.journal_path("2026-05-01").write_text("# one", encoding="utf-8")
    write_journal_meta(storage, "2026-05-01", source_fingerprint="stale-fingerprint")
    _write_message(storage, msg_id=2, text="world")

    plan = plan_run_days(
        storage,
        ["2026-05-01"],
        regenerate_if_stale=True,
        tz_name="Asia/Seoul",
    )
    assert plan[0]["action"] == "run"
    assert plan[0]["reason"] == "stale"
    assert plan[0]["is_stale"] is True


def test_plan_run_days_regenerate_when_generate_type_changes(tmp_path: Path) -> None:
    root = tmp_path / "data"
    storage = Storage(root)
    storage.ensure()
    storage.journal_path("2026-05-01").write_text("# one", encoding="utf-8")
    write_journal_meta(
        storage,
        "2026-05-01",
        source_fingerprint="fp",
        generate_type="combined",
    )

    skipped = plan_run_days(
        storage,
        ["2026-05-01"],
        skip_existing=True,
        generate_type="combined",
    )
    assert skipped[0]["action"] == "skip"

    changed = plan_run_days(
        storage,
        ["2026-05-01"],
        skip_existing=True,
        generate_type="both",
    )
    assert changed[0]["action"] == "run"
    assert changed[0]["reason"] == "fill_rooms"
    assert changed[0]["fill_missing"] is True
    assert changed[0]["previous_generate_type"] == "combined"

    rooms_only = Storage(tmp_path / "rooms")
    rooms_only.ensure()
    rooms_only.save_room_journal("2026-05-02", -1001, "# room\n")
    rooms_only.write_room_journals_index("2026-05-02", [{"id": "-1001", "title": "team"}])
    write_journal_meta(
        rooms_only,
        "2026-05-02",
        source_fingerprint="fp",
        generate_type="per_room",
    )
    fill_from_rooms = plan_run_days(
        rooms_only,
        ["2026-05-02"],
        skip_existing=True,
        generate_type="both",
    )
    assert fill_from_rooms[0]["reason"] == "fill_combined"
    assert fill_from_rooms[0]["fill_missing"] is True


def test_plan_run_days_skip_when_both_already_covers_request(tmp_path: Path) -> None:
    from worklog_agent.models import DailyBundle, DailyChat

    root = tmp_path / "data"
    storage = Storage(root)
    storage.ensure()
    storage.save_journal("2026-05-01", "# combined\n")
    storage.save_room_journal("2026-05-01", -1001, "# room\n")
    storage.write_room_journals_index(
        "2026-05-01",
        [{"id": "-1001", "title": "team"}],
    )
    write_journal_meta(
        storage,
        "2026-05-01",
        source_fingerprint="fp",
        generate_type="both",
    )
    storage.save_daily(
        DailyBundle(
            date="2026-05-01",
            timezone="Asia/Seoul",
            chats=[DailyChat(chat_id=-1001, title="team", message_count=1)],
            totals={"chats": 1, "messages": 1, "attachments": 0},
        )
    )

    for wanted in ("both", "combined", "per_room"):
        plan = plan_run_days(
            storage,
            ["2026-05-01"],
            skip_existing=True,
            generate_type=wanted,
        )
        assert plan[0]["action"] == "skip", wanted
        assert plan[0]["reason"] == "already_exists", wanted
        assert plan[0]["covers_generate_type"] is True, wanted


def test_plan_run_days_append_rooms_when_missing_room_journals(tmp_path: Path) -> None:
    from worklog_agent.models import DailyBundle, DailyChat

    root = tmp_path / "data"
    storage = Storage(root)
    storage.ensure()
    storage.save_room_journal("2026-05-01", -1001, "# room a\n")
    storage.write_room_journals_index(
        "2026-05-01",
        [{"id": "-1001", "title": "room-a"}],
    )
    write_journal_meta(
        storage,
        "2026-05-01",
        source_fingerprint="fp",
        generate_type="per_room",
    )
    storage.save_daily(
        DailyBundle(
            date="2026-05-01",
            timezone="Asia/Seoul",
            chats=[
                DailyChat(chat_id=-1001, title="room-a", message_count=1),
                DailyChat(chat_id=-1002, title="room-b", message_count=1),
            ],
            totals={"chats": 2, "messages": 2, "attachments": 0},
        )
    )

    plan = plan_run_days(
        storage,
        ["2026-05-01"],
        skip_existing=True,
        generate_type="per_room",
    )
    assert plan[0]["action"] == "run"
    assert plan[0]["reason"] == "append_rooms"
    assert plan[0]["append_rooms"] is True
    assert plan[0]["missing_rooms"] == ["-1002"]


def test_plan_run_days_stale_both_keeps_append_rooms(tmp_path: Path) -> None:
    root = tmp_path / "data"
    storage = Storage(root)
    storage.ensure()
    _write_message(storage, msg_id=1, text="hello")
    storage.journal_path("2026-05-01").write_text("# combined", encoding="utf-8")
    storage.save_room_journal("2026-05-01", -1001, "# room\n")
    storage.write_room_journals_index(
        "2026-05-01",
        [{"id": "-1001", "title": "team"}],
    )
    write_journal_meta(
        storage,
        "2026-05-01",
        source_fingerprint="stale-fingerprint",
        generate_type="both",
    )
    _write_message(storage, msg_id=2, text="world")

    plan = plan_run_days(
        storage,
        ["2026-05-01"],
        regenerate_if_stale=True,
        generate_type="both",
        tz_name="Asia/Seoul",
    )
    assert plan[0]["action"] == "run"
    assert plan[0]["reason"] == "stale_rooms"
    assert plan[0]["append_rooms"] is True


def test_plan_run_days_force_does_not_append_rooms(tmp_path: Path) -> None:
    root = tmp_path / "data"
    storage = Storage(root)
    storage.ensure()
    storage.save_room_journal("2026-05-01", -1001, "# room\n")
    storage.write_room_journals_index(
        "2026-05-01",
        [{"id": "-1001", "title": "team"}],
    )
    write_journal_meta(
        storage,
        "2026-05-01",
        source_fingerprint="fp",
        generate_type="per_room",
    )

    plan = plan_run_days(
        storage,
        ["2026-05-01"],
        force=True,
        generate_type="per_room",
    )
    assert plan[0]["action"] == "run"
    assert plan[0]["reason"] == "force"
    assert plan[0]["append_rooms"] is False


def test_plan_run_days_regenerate_if_stale_legacy_without_meta(tmp_path: Path) -> None:
    root = tmp_path / "data"
    storage = Storage(root)
    storage.journals.mkdir(parents=True, exist_ok=True)
    storage.journal_path("2026-05-01").write_text("# one", encoding="utf-8")

    plan = plan_run_days(
        storage,
        ["2026-05-01"],
        regenerate_if_stale=True,
        tz_name="Asia/Seoul",
    )
    assert plan[0]["action"] == "skip"
    assert plan[0]["reason"] == "legacy_no_meta"
