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


def stored_generate_type(storage: Storage, day: str, meta: dict | None = None) -> str | None:
    """Best-effort previous generate type from meta or on-disk journals."""
    payload = meta if meta is not None else (read_journal_meta(storage, day) if storage.has_any_journal(day) else None)
    if payload and payload.get("generate_type"):
        raw = str(payload.get("generate_type") or "").strip().lower()
        if raw in {"combined", "per_room", "both"}:
            return raw
    has_combined = storage.journal_path(day).exists()
    has_rooms = bool(storage.list_room_journals(day))
    if has_combined and has_rooms:
        return "both"
    if has_rooms:
        return "per_room"
    if has_combined:
        return "combined"
    return None


def plan_run_days(
    storage: Storage,
    dates: list[str],
    *,
    skip_existing: bool = False,
    regenerate_if_stale: bool = False,
    force: bool = False,
    generate_type: str = "combined",
    tz_name: str = "Asia/Seoul",
) -> list[dict]:
    from worklog_agent.journal import normalize_generate_type

    wanted = normalize_generate_type(generate_type)
    items: list[dict] = []
    for day in dates:
        has_journal = storage.has_any_journal(day)
        has_daily = storage.daily_path(day).exists()
        has_source = day_has_source(storage, day)
        meta = read_journal_meta(storage, day) if has_journal else None
        previous_type = stored_generate_type(storage, day, meta) if has_journal else None
        type_changed = bool(has_journal and previous_type and previous_type != wanted)
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
            fill_missing = False
        elif type_changed:
            action = "run"
            # 통합↔방마다에서 통합+방마다로 올릴 때는 없는 쪽만 채운다.
            fill_missing = wanted == "both" and previous_type in {"combined", "per_room"}
            if fill_missing:
                reason = "fill_combined" if previous_type == "per_room" else "fill_rooms"
            else:
                reason = "generate_type_changed"
        elif skip_existing and has_journal:
            action = "skip"
            reason = "already_exists"
            fill_missing = False
        elif regenerate_if_stale and has_journal:
            fill_missing = False
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
            fill_missing = False
        items.append(
            {
                "date": day,
                "action": action,
                "reason": reason,
                "has_journal": has_journal,
                "has_daily": has_daily,
                "has_source": has_source,
                "is_stale": is_stale,
                "generate_type": wanted,
                "previous_generate_type": previous_type,
                "generate_type_changed": type_changed,
                "fill_missing": fill_missing,
            }
        )
    return items


def dates_to_run(plan: list[dict]) -> list[str]:
    return [item["date"] for item in plan if item["action"] == "run"]
