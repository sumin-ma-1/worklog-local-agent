from __future__ import annotations

import re
import secrets
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from worklog_agent.config import AppConfig
from worklog_agent.users import telegram_linked, user_config, users_dir

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_TARGETS = frozenset({"today", "yesterday"})


class ScheduleError(ValueError):
    pass


def schedules_path(user_root: Path) -> Path:
    return Path(user_root) / "schedules.json"


def _legacy_nested_schedules_path(user_root: Path) -> Path:
    """과거 버그: users/<id>/users/<id>/schedules.json"""
    root = Path(user_root)
    return root / "users" / root.name / "schedules.json"


def migrate_legacy_schedules(user_root: Path) -> None:
    """중첩 경로에 남은 예약을 올바른 users/<id>/schedules.json 으로 옮긴다."""
    root = Path(user_root)
    correct = schedules_path(root)
    legacy = _legacy_nested_schedules_path(root)
    if not legacy.is_file():
        return
    if correct.is_file():
        try:
            legacy.unlink()
        except OSError:
            pass
        return
    try:
        correct.parent.mkdir(parents=True, exist_ok=True)
        legacy.replace(correct)
        nested_dir = legacy.parent
        if nested_dir.is_dir() and not any(nested_dir.iterdir()):
            nested_dir.rmdir()
        parent = nested_dir.parent
        if parent.is_dir() and parent.name == "users" and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        pass


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    import json

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _write_json(path: Path, payload: Any) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_schedule_time(value: str) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"^\d{2}:\d{2}:\d{2}$", text):
        text = text[:5]
    if not _TIME_RE.fullmatch(text):
        raise ScheduleError("실행 시각은 HH:MM 형식이어야 합니다.")
    return text


def parse_schedule_target(value: str) -> str:
    target = str(value or "").strip().lower()
    if target not in _TARGETS:
        raise ScheduleError("대상은 today 또는 yesterday 여야 합니다.")
    return target


def load_schedules(user_root: Path) -> list[dict[str, Any]]:
    migrate_legacy_schedules(user_root)
    raw = _read_json(schedules_path(user_root), {"schedules": []})
    items = raw.get("schedules") if isinstance(raw, dict) else []
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def save_schedules(user_root: Path, schedules: list[dict[str, Any]]) -> None:
    _write_json(schedules_path(user_root), {"schedules": schedules})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_schedule(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item.get("id") or ""),
        "name": str(item.get("name") or "").strip(),
        "enabled": bool(item.get("enabled", True)),
        "time": parse_schedule_time(str(item.get("time") or "09:00")),
        "target": parse_schedule_target(str(item.get("target") or "yesterday")),
        "skip_existing": bool(item.get("skip_existing", True)),
        "regenerate_if_stale": bool(item.get("regenerate_if_stale", False)),
        "force": bool(item.get("force", False)),
        "created_at": str(item.get("created_at") or _now_iso()),
        "updated_at": str(item.get("updated_at") or _now_iso()),
        "last_run_at": item.get("last_run_at"),
        "last_slot": item.get("last_slot"),
        "last_status": item.get("last_status"),
        "last_message": item.get("last_message"),
    }


def create_schedule(user_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ScheduleError("예약 이름을 입력하세요.")
    if len(name) > 64:
        raise ScheduleError("예약 이름은 64자 이하여야 합니다.")
    schedules = load_schedules(user_root)
    now = _now_iso()
    item = normalize_schedule(
        {
            **payload,
            "id": secrets.token_hex(8),
            "name": name,
            "created_at": now,
            "updated_at": now,
        }
    )
    schedules.append(item)
    save_schedules(user_root, schedules)
    return item


def update_schedule(user_root: Path, schedule_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    schedules = load_schedules(user_root)
    for index, current in enumerate(schedules):
        if str(current.get("id")) != schedule_id:
            continue
        merged = {**current, **payload, "id": schedule_id}
        if "name" in payload:
            name = str(payload.get("name") or "").strip()
            if not name:
                raise ScheduleError("예약 이름을 입력하세요.")
            merged["name"] = name
        item = normalize_schedule({**merged, "updated_at": _now_iso()})
        schedules[index] = item
        save_schedules(user_root, schedules)
        return item
    raise ScheduleError("예약을 찾을 수 없습니다.")


def delete_schedule(user_root: Path, schedule_id: str) -> bool:
    schedules = load_schedules(user_root)
    kept = [item for item in schedules if str(item.get("id")) != schedule_id]
    if len(kept) == len(schedules):
        return False
    save_schedules(user_root, kept)
    return True


def slot_key(day: date, time_str: str) -> str:
    return f"{day.isoformat()}T{time_str}"


def schedule_target_dates(target: str, tz_name: str, *, now: datetime | None = None) -> list[str]:
    target = parse_schedule_target(target)
    tz = ZoneInfo(tz_name)
    local_now = (now or datetime.now(tz)).astimezone(tz)
    day = local_now.date()
    if target == "today":
        return [day.isoformat()]
    return [(day - timedelta(days=1)).isoformat()]


def is_schedule_due(schedule: dict[str, Any], tz_name: str, *, now: datetime | None = None) -> bool:
    if not schedule.get("enabled", True):
        return False
    tz = ZoneInfo(tz_name)
    local_now = (now or datetime.now(tz)).astimezone(tz)
    time_str = parse_schedule_time(str(schedule.get("time") or "09:00"))
    hour, minute = (int(part) for part in time_str.split(":"))
    scheduled_at = datetime.combine(
        local_now.date(),
        time(hour, minute),
        tzinfo=local_now.tzinfo,
    )
    key = slot_key(local_now.date(), time_str)
    if schedule.get("last_slot") == key:
        return False
    return local_now >= scheduled_at


def mark_schedule_result(
    user_root: Path,
    schedule_id: str,
    *,
    status: str,
    message: str,
    tz_name: str,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    schedules = load_schedules(user_root)
    tz = ZoneInfo(tz_name)
    local_now = (now or datetime.now(tz)).astimezone(tz)
    for index, current in enumerate(schedules):
        if str(current.get("id")) != schedule_id:
            continue
        time_str = parse_schedule_time(str(current.get("time") or "09:00"))
        updated = {
            **current,
            "last_run_at": _now_iso(),
            "last_slot": slot_key(local_now.date(), time_str),
            "last_status": status,
            "last_message": message,
            "updated_at": _now_iso(),
        }
        schedules[index] = updated
        save_schedules(user_root, schedules)
        return updated
    return None


def iter_user_ids(data_root: Path) -> list[str]:
    base = users_dir(data_root)
    if not base.is_dir():
        return []
    return sorted(path.name for path in base.iterdir() if path.is_dir())


def tick_all_schedules(
    base_config: AppConfig,
    runtime_for: Callable[[str], Any],
    *,
    now: datetime | None = None,
    wait: bool = False,
) -> list[dict[str, Any]]:
    from worklog_agent.web.run_service import EmptyPlanError, RunBusyError, enqueue_planned_run

    results: list[dict[str, Any]] = []
    for user_id in iter_user_ids(base_config.data_root):
        root = users_dir(base_config.data_root) / user_id
        cfg = user_config(base_config, user_id)
        if not telegram_linked(root, cfg.telegram.session_name):
            continue
        schedules = load_schedules(root)
        if not schedules:
            continue
        runtime = runtime_for(user_id)
        for schedule in schedules:
            schedule_id = str(schedule.get("id") or "")
            if not schedule_id or not is_schedule_due(schedule, cfg.timezone, now=now):
                continue
            dates = schedule_target_dates(str(schedule.get("target") or "yesterday"), cfg.timezone, now=now)
            body = {
                "dates": dates,
                "skip_existing": bool(schedule.get("skip_existing", True)),
                "regenerate_if_stale": bool(schedule.get("regenerate_if_stale", False)),
                "force": bool(schedule.get("force", False)),
            }
            try:
                enqueue_planned_run(
                    base_config,
                    user_id,
                    cfg,
                    runtime,
                    body,
                    wait=wait,
                )
                mark_schedule_result(
                    root,
                    schedule_id,
                    status="started",
                    message="예약 실행을 시작했습니다.",
                    tz_name=cfg.timezone,
                    now=now,
                )
                results.append({"user_id": user_id, "schedule_id": schedule_id, "status": "started"})
            except RunBusyError:
                results.append({"user_id": user_id, "schedule_id": schedule_id, "status": "busy"})
            except EmptyPlanError as exc:
                mark_schedule_result(
                    root,
                    schedule_id,
                    status="skipped",
                    message=str(exc),
                    tz_name=cfg.timezone,
                    now=now,
                )
                results.append({"user_id": user_id, "schedule_id": schedule_id, "status": "skipped"})
            except Exception as exc:
                mark_schedule_result(
                    root,
                    schedule_id,
                    status="error",
                    message=str(exc),
                    tz_name=cfg.timezone,
                    now=now,
                )
                results.append({"user_id": user_id, "schedule_id": schedule_id, "status": "error"})
    return results
