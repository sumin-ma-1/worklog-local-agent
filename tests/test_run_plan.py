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
