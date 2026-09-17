from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from worklog_agent.config import AppConfig, chat_ref_key, normalize_chat_ref
from worklog_agent.schedules import (
    ScheduleError,
    create_schedule,
    delete_schedule,
    load_schedules,
    update_schedule,
)
from worklog_agent.storage import Storage
from worklog_agent.users import add_user_chat, load_user_chats, remove_user_chat
from worklog_agent.web.chat_intent import parse_chat_intent

logger = logging.getLogger(__name__)

_MAX_MEMORY_TURNS = 8  # last 4 user/assistant exchanges


def chat_memory_path(user_root: Path) -> Path:
    return Path(user_root) / "chat_memory.json"


def load_chat_memory(user_root: Path, *, limit: int = _MAX_MEMORY_TURNS) -> list[dict[str, str]]:
    path = chat_memory_path(user_root)
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    turns = raw.get("turns") if isinstance(raw, dict) else []
    if not isinstance(turns, list):
        return []
    out: list[dict[str, str]] = []
    for item in turns:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        content = str(item.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            out.append({"role": role, "content": content})
    return out[-max(0, int(limit)) :]


def save_chat_memory(user_root: Path, turns: list[dict[str, str]]) -> None:
    root = Path(user_root)
    root.mkdir(parents=True, exist_ok=True)
    cleaned = [
        {"role": t["role"], "content": t["content"]}
        for t in turns
        if t.get("role") in {"user", "assistant"} and str(t.get("content") or "").strip()
    ][-_MAX_MEMORY_TURNS:]
    chat_memory_path(root).write_text(
        json.dumps(
            {
                "turns": cleaned,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def append_chat_memory(user_root: Path, question: str, answer: str) -> None:
    turns = load_chat_memory(user_root, limit=_MAX_MEMORY_TURNS)
    turns.append({"role": "user", "content": str(question).strip()})
    turns.append({"role": "assistant", "content": str(answer).strip()[:2000]})
    save_chat_memory(user_root, turns)


def clear_chat_memory(user_root: Path) -> None:
    save_chat_memory(user_root, [])


def _format_history(turns: list[dict[str, str]]) -> str:
    if not turns:
        return ""
    lines: list[str] = ["최근 대화:"]
    for item in turns:
        who = "사용자" if item["role"] == "user" else "비서"
        lines.append(f"{who}: {item['content']}")
    return "\n".join(lines)


def _remember(cfg: AppConfig, question: str, answer: str, *, remember: bool) -> None:
    if not remember:
        return
    try:
        append_chat_memory(cfg.data_root, question, answer)
    except Exception:
        logger.exception("챗 문맥 저장 실패")


def _room_catalog(cfg: AppConfig) -> list[dict[str, Any]]:
    storage = Storage(cfg.data_root)
    storage.ensure()
    titles = storage.load_chat_titles()
    watched = load_user_chats(cfg.data_root)
    watched_keys = {chat_ref_key(item) for item in watched}
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    for ref in watched:
        key = chat_ref_key(ref)
        title = titles.get(key) or str(ref)
        if isinstance(ref, int):
            meta = storage.watched_chat_meta(ref)
            if meta.get("title"):
                title = str(meta["title"])
        rows.append({"id": ref, "key": key, "title": title, "watched": True})
        seen.add(key)

    for key, title in titles.items():
        if key in seen:
            continue
        try:
            ref = normalize_chat_ref(key)
        except ValueError:
            continue
        rows.append(
            {
                "id": ref,
                "key": chat_ref_key(ref),
                "title": str(title or key),
                "watched": key in watched_keys,
            }
        )
        seen.add(key)
    return rows


def _match_rooms(query: str, catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
    q = str(query or "").strip().lower()
    if not q:
        return []
    exact: list[dict[str, Any]] = []
    partial: list[dict[str, Any]] = []
    for row in catalog:
        title = str(row.get("title") or "").strip().lower()
        key = str(row.get("key") or "").lower()
        if q == title or q == key:
            exact.append(row)
        elif q in title or title in q or q in key:
            partial.append(row)
    return exact or partial


def _apply_watch_intent(cfg: AppConfig, intent_info: dict[str, Any]) -> dict[str, Any]:
    action = str(intent_info.get("action") or "").strip().lower()
    if action not in {"on", "off"}:
        return {
            "intent": "watch",
            "answer": "수집방을 켜거나 끄는 요청만 처리할 수 있습니다.",
            "days": [],
            "generate": None,
            "watch": None,
        }

    if intent_info.get("all") and action == "off":
        chats = load_user_chats(cfg.data_root)
        if not chats:
            answer = "이미 수집 대상이 비어 있습니다."
            return {
                "intent": "watch",
                "answer": answer,
                "days": [],
                "generate": None,
                "watch": {"action": "off", "all": True, "rooms": []},
            }
        catalog = {chat_ref_key(row["id"]): row for row in _room_catalog(cfg)}
        removed: list[dict[str, Any]] = []
        for ref in list(chats):
            key = chat_ref_key(ref)
            remove_user_chat(cfg.data_root, ref)
            row = catalog.get(key) or {"id": ref, "key": key, "title": str(ref)}
            removed.append({"id": row["id"], "title": row.get("title") or str(ref)})
        answer = f"수집방 {len(removed)}개를 모두 껐습니다."
        return {
            "intent": "watch",
            "answer": answer,
            "days": [],
            "generate": None,
            "watch": {"action": "off", "all": True, "rooms": removed},
        }

    query = str(intent_info.get("room_query") or "").strip()
    if not query:
        hint = (
            "어떤 방을 켤까요? 예: 「금형방 수집 켜줘」"
            if action == "on"
            else "어떤 방을 끌까요? 예: 「금형방 수집 꺼줘」 / 「수집방 전부 꺼줘」"
        )
        return {
            "intent": "watch",
            "answer": hint,
            "days": [],
            "generate": None,
            "watch": None,
        }

    matches = _match_rooms(query, _room_catalog(cfg))
    if not matches:
        answer = (
            f"「{query}」에 해당하는 방을 찾지 못했습니다. "
            "텔레그램 탭에서 대화 목록을 새로고침한 뒤 방 이름으로 다시 말해 주세요."
        )
        return {
            "intent": "watch",
            "answer": answer,
            "days": [],
            "generate": None,
            "watch": None,
        }
    if len(matches) > 1:
        names = ", ".join(f"「{row.get('title') or row['id']}」" for row in matches[:5])
        return {
            "intent": "watch",
            "answer": f"여러 방이 맞습니다: {names}. 이름을 더 구체적으로 말해 주세요.",
            "days": [],
            "generate": None,
            "watch": None,
        }

    room = matches[0]
    title = str(room.get("title") or room["id"])
    ref = room["id"]
    watched = bool(room.get("watched"))

    if action == "on":
        if watched:
            answer = f"「{title}」은(는) 이미 수집 중입니다."
        else:
            add_user_chat(cfg.data_root, ref)
            answer = f"「{title}」수집을 켰습니다."
    else:
        if not watched:
            answer = f"「{title}」은(는) 수집 대상이 아닙니다."
        else:
            remove_user_chat(cfg.data_root, ref)
            answer = f"「{title}」수집을 껐습니다."

    return {
        "intent": "watch",
        "answer": answer,
        "days": [],
        "generate": None,
        "watch": {
            "action": action,
            "all": False,
            "rooms": [{"id": ref, "title": title}],
        },
    }


def _match_schedules(
    schedules: list[dict[str, Any]],
    *,
    name_query: str | None,
    time_str: str | None,
) -> list[dict[str, Any]]:
    rows = list(schedules)
    if time_str:
        rows = [item for item in rows if str(item.get("time") or "") == time_str]
    q = str(name_query or "").strip().lower()
    if not q:
        return rows
    exact: list[dict[str, Any]] = []
    partial: list[dict[str, Any]] = []
    for item in rows:
        name = str(item.get("name") or "").strip().lower()
        if not name:
            continue
        if q == name:
            exact.append(item)
        elif q in name or name in q:
            partial.append(item)
    return exact or partial


def _format_schedule_line(item: dict[str, Any]) -> str:
    target = "오늘" if item.get("target") == "today" else "어제"
    state = "ON" if item.get("enabled", True) else "OFF"
    return f"· {item.get('name')} ({item.get('time')} / {target} / {state})"


def _apply_schedule_intent(cfg: AppConfig, intent_info: dict[str, Any]) -> dict[str, Any]:
    action = str(intent_info.get("action") or "").strip().lower()
    schedules = load_schedules(cfg.data_root)
    name_query = intent_info.get("name_query")
    time_str = intent_info.get("time")

    if action == "list":
        if not schedules:
            answer = "등록된 예약이 없습니다. 예: 「매일 9시 어제 일지 예약 추가해줘」"
        else:
            lines = ["예약 목록:"] + [_format_schedule_line(item) for item in schedules]
            answer = "\n".join(lines)
        return {
            "intent": "schedule",
            "answer": answer,
            "days": [],
            "generate": None,
            "schedule": {"action": "list", "items": schedules},
        }

    if action == "create":
        if not time_str:
            return {
                "intent": "schedule",
                "answer": "실행 시각을 알려 주세요. 예: 「매일 9시 어제 일지 예약 추가해줘」",
                "days": [],
                "generate": None,
                "schedule": None,
            }
        target = str(intent_info.get("target") or "yesterday")
        target_label = "오늘" if target == "today" else "어제"
        name = str(name_query or "").strip() or f"{target_label} {time_str}"
        try:
            item = create_schedule(
                cfg.data_root,
                {
                    "name": name,
                    "enabled": True,
                    "time": time_str,
                    "target": target,
                    "generate_type": "combined",
                    "skip_existing": True,
                    "regenerate_if_stale": False,
                    "force": False,
                },
            )
        except ScheduleError as exc:
            return {
                "intent": "schedule",
                "answer": str(exc),
                "days": [],
                "generate": None,
                "schedule": None,
            }
        answer = f"예약 「{item['name']}」을(를) 추가했습니다. ({item['time']} / {target_label})"
        return {
            "intent": "schedule",
            "answer": answer,
            "days": [],
            "generate": None,
            "schedule": {"action": "create", "items": [item]},
        }

    if action in {"enable", "disable", "delete"}:
        matches = _match_schedules(schedules, name_query=name_query, time_str=time_str)
        if not matches:
            hint = "예약 이름을 알려 주세요. 「예약 목록 보여줘」로 확인할 수 있습니다."
            if name_query or time_str:
                hint = "해당하는 예약을 찾지 못했습니다. 「예약 목록 보여줘」로 확인해 주세요."
            return {
                "intent": "schedule",
                "answer": hint,
                "days": [],
                "generate": None,
                "schedule": None,
            }
        if len(matches) > 1:
            names = ", ".join(f"「{item.get('name')}」" for item in matches[:5])
            return {
                "intent": "schedule",
                "answer": f"여러 예약이 맞습니다: {names}. 이름이나 시각을 더 구체적으로 말해 주세요.",
                "days": [],
                "generate": None,
                "schedule": None,
            }
        item = matches[0]
        sid = str(item.get("id") or "")
        title = str(item.get("name") or sid)
        try:
            if action == "delete":
                if not delete_schedule(cfg.data_root, sid):
                    raise ScheduleError("예약을 찾을 수 없습니다.")
                answer = f"예약 「{title}」을(를) 삭제했습니다."
            elif action == "enable":
                if item.get("enabled", True):
                    answer = f"예약 「{title}」은(는) 이미 활성화되어 있습니다."
                else:
                    update_schedule(cfg.data_root, sid, {"enabled": True})
                    answer = f"예약 「{title}」을(를) 활성화했습니다."
            else:
                if not item.get("enabled", True):
                    answer = f"예약 「{title}」은(는) 이미 비활성화되어 있습니다."
                else:
                    update_schedule(cfg.data_root, sid, {"enabled": False})
                    answer = f"예약 「{title}」을(를) 비활성화했습니다."
        except ScheduleError as exc:
            return {
                "intent": "schedule",
                "answer": str(exc),
                "days": [],
                "generate": None,
                "schedule": None,
            }
        return {
            "intent": "schedule",
            "answer": answer,
            "days": [],
            "generate": None,
            "schedule": {"action": action, "items": [item]},
        }

    return {
        "intent": "schedule",
        "answer": "예약 추가·활성/비활성·삭제만 처리할 수 있습니다.",
        "days": [],
        "generate": None,
        "schedule": None,
    }


async def answer_journal_question(
    cfg: AppConfig,
    question: str,
    *,
    history: list[dict[str, str]] | None = None,
    remember: bool = True,
) -> dict[str, Any]:
    """Shared journal chat handler for dashboard and Telegram bot."""
    text = str(question or "").strip()
    intent_info = parse_chat_intent(text, tz_name=cfg.timezone)
    if intent_info.get("intent") == "watch":
        result = _apply_watch_intent(cfg, intent_info)
        _remember(cfg, text, str(result.get("answer") or ""), remember=remember)
        return result

    if intent_info.get("intent") == "schedule":
        result = _apply_schedule_intent(cfg, intent_info)
        _remember(cfg, text, str(result.get("answer") or ""), remember=remember)
        return result

    if intent_info.get("intent") == "generate" and intent_info.get("dates"):
        label = intent_info.get("label") or intent_info["dates"][0]
        day = intent_info["dates"][0]
        answer = f"{label}({day}) 일지를 생성할까요? 확인 후 실행해 주세요."
        _remember(cfg, text, answer, remember=remember)
        return {
            "intent": "generate",
            "answer": answer,
            "days": [],
            "generate": {
                "dates": list(intent_info["dates"]),
                "label": label,
            },
        }

    storage = Storage(cfg.data_root)
    dates = sorted(set(storage.list_journal_dates()) | set(storage.list_daily_dates()), reverse=True)
    snippets: list[str] = []
    for day in dates[:40]:
        chunks: list[str] = []
        if storage.journal_path(day).exists():
            body = (storage.read_journal(day) or "").strip()
            if body:
                chunks.append(body[:1200])
        for room in storage.list_room_journals(day)[:8]:
            try:
                body = (storage.read_room_journal(day, room["id"]) or "").strip()
            except FileNotFoundError:
                continue
            if body:
                chunks.append(f"[{room['title']}]\n{body[:800]}")
        if not chunks:
            continue
        snippets.append(f"## {day}\n" + "\n\n".join(chunks))
    if not snippets:
        answer = (
            "아직 검색할 일지가 없습니다. 먼저 일지를 생성해 주세요. "
            "「어제 일지 생성해줘」처럼 요청할 수도 있습니다."
        )
        _remember(cfg, text, answer, remember=remember)
        return {
            "intent": "search",
            "answer": answer,
            "days": [],
            "generate": None,
        }
    corpus = "\n\n".join(snippets)
    turns = history if history is not None else load_chat_memory(cfg.data_root)
    history_block = _format_history(turns)
    system = (
        "당신은 사용자의 업무 일지 비서입니다. "
        "일지 검색·요약이 주 업무이고, 간단한 인사나 짧은 일상 대화에도 자연스럽고 친절하게 짧게 응답하세요. "
        "최근 대화가 있으면 그 문맥을 이어서 이해하고 답하세요. "
        "일지 내용만 근거로 한국어로 짧고 정확하게 답하세요. "
        "인사·잡담에는 DAY 줄을 붙이지 마세요. "
        "일지 관련 답변에서 관련 날짜가 있으면 답변 끝에 한 줄로 DAY:YYYY-MM-DD 형식으로 적어 주세요. "
        "여러 날이면 가장 관련 있는 하루만 적으세요. 근거가 없으면 모른다고 말하세요. "
        "일지 생성·수집방 on/off·예약 추가/활성/삭제 요청은 서버가 따로 처리하므로, 필요하면 짧게 안내만 하세요."
    )
    parts = []
    if history_block:
        parts.append(history_block)
    parts.append(f"질문: {text}")
    parts.append(f"일지:\n{corpus}")
    user_prompt = "\n\n".join(parts)
    from worklog_agent.ollama import OllamaError, chat as ollama_chat

    try:
        answer = await ollama_chat(cfg, system, user_prompt, model=cfg.journal.ollama.model)
    except OllamaError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"일지 질의 실패: {exc}") from exc

    days: list[str] = []
    match = re.search(r"DAY:(\d{4}-\d{2}-\d{2})", answer or "")
    if match:
        days.append(match.group(1))
        answer = re.sub(r"\n?DAY:\d{4}-\d{2}-\d{2}\s*$", "", answer).strip()
    final = answer or "답변을 만들지 못했습니다."
    _remember(cfg, text, final, remember=remember)
    return {
        "intent": "search",
        "answer": final,
        "days": days,
        "generate": None,
    }
