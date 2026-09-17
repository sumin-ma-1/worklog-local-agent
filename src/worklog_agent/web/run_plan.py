from __future__ import annotations

from datetime import date, timedelta

from worklog_agent.journal_meta import read_journal_meta
from worklog_agent.source_fingerprint import day_source_fingerprint
from worklog_agent.storage import Storage

_MAX_RANGE_DAYS = 31


def _parse_day(day: str) -> date:
    return date.fromisoformat(day)


def expand_date_range(start: str, end: str) -> list[str]:
    begin = _parse_day(start)
    finish = _parse_day(end)
    if finish < begin:
        begin, finish = finish, begin
    days: list[str] = []
    current = begin
    while current <= finish:
        days.append(current.isoformat())
        current += timedelta(days=1)
    if len(days) > _MAX_RANGE_DAYS:
        raise ValueError(f"최대 {_MAX_RANGE_DAYS}일까지 선택할 수 있습니다.")
    return days


def resolve_run_dates(
    *,
    date: str | None = None,
    start: str | None = None,
    end: str | None = None,
    dates: list[str] | None = None,
) -> list[str]:
    if dates:
        normalized = sorted({_parse_day(item).isoformat() for item in dates}, reverse=True)
        if len(normalized) > _MAX_RANGE_DAYS:
            raise ValueError(f"최대 {_MAX_RANGE_DAYS}일까지 선택할 수 있습니다.")
        return normalized
    if start and end:
        return expand_date_range(start, end)
    if date:
        return [_parse_day(date).isoformat()]
    raise ValueError("생성할 날짜를 지정하세요.")


def day_has_source(storage: Storage, day: str) -> bool:
    if storage.daily_path(day).exists():
        return True
    root = storage.raw
    if not root.is_dir():
        return False
    for chat_dir in root.iterdir():
        if not chat_dir.is_dir():
            continue
        messages = chat_dir / "messages.jsonl"
        if messages.is_file() and messages.stat().st_size > 0:
            return True
    return False


def plan_run_days(
    storage: Storage,
    dates: list[str],
    *,
    skip_existing: bool = False,
    regenerate_if_stale: bool = False,
    force: bool = False,
    tz_name: str = "Asia/Seoul",
) -> list[dict]:
    items: list[dict] = []
    for day in dates:
        has_journal = storage.has_any_journal(day)
        has_daily = storage.daily_path(day).exists()
        has_source = day_has_source(storage, day)
        meta = read_journal_meta(storage, day) if has_journal else None
        current_fp = day_source_fingerprint(storage, day, tz_name) if has_journal else None
        stored_fp = meta.get("source_fingerprint") if meta else None
        is_stale = bool(
            has_journal
            and meta
            and stored_fp is not None
            and current_fp is not None
            and stored_fp != current_fp
        )
        if force:
            action = "run"
            reason = "force"
        elif skip_existing and has_journal:
            action = "skip"
            reason = "already_exists"
        elif regenerate_if_stale and has_journal:
            if not meta or stored_fp is None:
                action = "skip"
                reason = "legacy_no_meta"
            elif is_stale:
                action = "run"
                reason = "stale"
            else:
                action = "skip"
                reason = "up_to_date"
        else:
            action = "run"
            reason = "missing_journal" if not has_journal else "regenerate"
        items.append(
            {
                "date": day,
                "action": action,
                "reason": reason,
                "has_journal": has_journal,
                "has_daily": has_daily,
                "has_source": has_source,
                "is_stale": is_stale,
            }
        )
    return items


def dates_to_run(plan: list[dict]) -> list[str]:
    return [item["date"] for item in plan if item["action"] == "run"]
