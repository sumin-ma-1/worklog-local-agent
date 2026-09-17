from __future__ import annotations

import logging
import re
from typing import Any

from worklog_agent.config import AppConfig
from worklog_agent.storage import Storage
from worklog_agent.web.chat_intent import parse_chat_intent

logger = logging.getLogger(__name__)


async def answer_journal_question(cfg: AppConfig, question: str) -> dict[str, Any]:
    """Shared journal chat handler for dashboard and Telegram bot."""
    text = str(question or "").strip()
    intent_info = parse_chat_intent(text, tz_name=cfg.timezone)
    if intent_info.get("intent") == "generate" and intent_info.get("dates"):
        label = intent_info.get("label") or intent_info["dates"][0]
        day = intent_info["dates"][0]
        return {
            "intent": "generate",
            "answer": f"{label}({day}) 일지를 생성할까요? 확인 후 실행해 주세요.",
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
        return {
            "intent": "search",
            "answer": "아직 검색할 일지가 없습니다. 먼저 일지를 생성해 주세요. "
            "「어제 일지 생성해줘」처럼 요청할 수도 있습니다.",
            "days": [],
            "generate": None,
        }
    corpus = "\n\n".join(snippets)
    system = (
        "당신은 사용자의 업무 일지 비서입니다. "
        "일지 검색·요약이 주 업무이고, 간단한 인사나 짧은 일상 대화에도 자연스럽고 친절하게 짧게 응답하세요. "
        "일지 내용만 근거로 한국어로 짧고 정확하게 답하세요. "
        "인사·잡담에는 DAY 줄을 붙이지 마세요. "
        "일지 관련 답변에서 관련 날짜가 있으면 답변 끝에 한 줄로 DAY:YYYY-MM-DD 형식으로 적어 주세요. "
        "여러 날이면 가장 관련 있는 하루만 적으세요. 근거가 없으면 모른다고 말하세요. "
        "일지 생성 요청은 서버가 따로 처리하므로, 생성이 필요하면 짧게 안내만 하세요."
    )
    user_prompt = f"질문: {text}\n\n일지:\n{corpus}"
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
    return {
        "intent": "search",
        "answer": answer or "답변을 만들지 못했습니다.",
        "days": days,
        "generate": None,
    }
