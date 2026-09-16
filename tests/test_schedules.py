from datetime import datetime, timezone
from pathlib import Path
import pytest

from worklog_agent.config import load_config
from worklog_agent.schedules import (
    ScheduleError,
    create_schedule,
    is_schedule_due,
    load_schedules,
    schedule_target_dates,
    slot_key,
    tick_all_schedules,
)
from worklog_agent.users import ensure_user_root
from worklog_agent.web.run_service import RuntimeRegistry


def test_create_and_load_schedule(tmp_path: Path) -> None:
    root = ensure_user_root(tmp_path / "data", "u1")
    item = create_schedule(
        root,
        {"name": "매일", "time": "21:00", "target": "yesterday", "skip_existing": True},
    )
    assert item["name"] == "매일"
    assert item["time"] == "21:00"
    schedules = load_schedules(root)
    assert len(schedules) == 1
    assert schedules[0]["id"] == item["id"]


def test_parse_time_accepts_seconds() -> None:
    from worklog_agent.schedules import parse_schedule_time

    assert parse_schedule_time("09:30:00") == "09:30"


def test_schedule_target_dates_yesterday() -> None:
    now = datetime(2026, 3, 2, 10, 0, tzinfo=timezone.utc)
    assert schedule_target_dates("yesterday", "Asia/Seoul", now=now) == ["2026-03-01"]


def test_is_schedule_due_once_per_day() -> None:
    schedule = {"enabled": True, "time": "09:00", "last_slot": None}
    now = datetime(2026, 3, 2, 9, 5, tzinfo=timezone.utc)
    assert is_schedule_due(schedule, "UTC", now=now) is True
    schedule["last_slot"] = slot_key(now.date(), "09:00")
    assert is_schedule_due(schedule, "UTC", now=now) is False


def test_create_schedule_rejects_bad_time(tmp_path: Path) -> None:
    root = ensure_user_root(tmp_path / "data", "u1")
    with pytest.raises(ScheduleError):
        create_schedule(root, {"name": "bad", "time": "25:99"})


def test_tick_all_schedules_skips_empty_plan(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    data_root = tmp_path / "data"
    config_path.write_text(
        "\n".join(
            [
                "timezone: UTC",
                "telegram:",
                "  session_name: worklog",
                "  chats: []",
                "storage:",
                f"  root: {data_root}",
                "collect:",
                "  lookback_days: 7",
                "journal:",
                "  ollama:",
                "    host: http://127.0.0.1:11434",
                "    model: test",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    config = load_config(config_path)
    user_root = ensure_user_root(data_root, "u1")
    (user_root / "sessions").mkdir(parents=True, exist_ok=True)
    (user_root / "sessions" / "worklog.session").write_bytes(b"session")
    create_schedule(
        user_root,
        {"name": "daily", "time": "00:00", "target": "yesterday", "skip_existing": True},
    )
    journals = user_root / "journals"
    journals.mkdir(parents=True, exist_ok=True)
    (journals / "2026-03-01.md").write_text("# done", encoding="utf-8")
    now = datetime(2026, 3, 2, 1, 0, tzinfo=timezone.utc)
    registry = RuntimeRegistry()
    results = tick_all_schedules(config, registry.runtime_for, now=now)
    assert results[0]["status"] == "skipped"
    saved = load_schedules(user_root)[0]
    assert saved["last_status"] == "skipped"
