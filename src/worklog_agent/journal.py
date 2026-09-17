from __future__ import annotations

import json

from worklog_agent.config import AppConfig
from worklog_agent.journal_prompt import default_system_prompt
from worklog_agent.models import DailyBundle, DailyChat
from worklog_agent.ollama import chat as ollama_chat

JOURNAL_SYSTEM_PROMPT = default_system_prompt()

GENERATE_TYPES = ("combined", "per_room", "both")
GENERATE_TYPE_LABELS = {
    "combined": "통합 한 번",
    "per_room": "방마다",
    "both": "통합 + 방마다",
}


def normalize_generate_type(value: str | None) -> str:
    raw = str(value or "combined").strip().lower()
    aliases = {
        "통합": "combined",
        "통합 한 번": "combined",
        "all": "combined",
        "combined": "combined",
        "방마다": "per_room",
        "room": "per_room",
        "rooms": "per_room",
        "per_room": "per_room",
        "per-room": "per_room",
        "통합+방마다": "both",
        "통합 + 방마다": "both",
        "both": "both",
        "all+rooms": "both",
    }
    normalized = aliases.get(raw, raw)
    if normalized not in GENERATE_TYPES:
        raise ValueError("생성 유형은 통합 한 번, 방마다, 통합 + 방마다 중 하나여야 합니다.")
    return normalized


def bundle_for_chat(bundle: DailyBundle, chat: DailyChat) -> DailyBundle:
    return DailyBundle(
        date=bundle.date,
        timezone=bundle.timezone,
        chats=[chat],
        totals={
            "chats": 1,
            "messages": chat.message_count,
            "attachments": len(chat.attachments),
        },
    )


def render_draft(bundle: DailyBundle) -> str:
    room_suffix = ""
    if len(bundle.chats) == 1:
        room_suffix = f" · {bundle.chats[0].title}"
    lines = [
        f"# 업무 일지 ({bundle.date}){room_suffix}",
        "",
        "## 요약",
        (
            f"{bundle.timezone} 기준 채팅 {bundle.totals.get('chats', 0)}곳, "
            f"메시지 {bundle.totals.get('messages', 0)}개, "
            f"첨부 {bundle.totals.get('attachments', 0)}개."
        ),
        "",
        "## 수행 업무",
    ]
    if not bundle.chats:
        lines.append("수집된 메시지가 없습니다.")
        lines.extend(["", "## 논의 / 결정 사항", "없음", "", "## 요청 / 협업", "없음"])
        lines.extend(["", "## 이슈 및 리스크", "없음", "", "## 산출물 / 첨부", "없음"])
        lines.extend(["", "## 다음 액션", "없음", ""])
        return "\n".join(lines)

    for chat in bundle.chats:
        if len(bundle.chats) > 1:
            lines.append(f"### {chat.title}")
        for msg in chat.messages:
            time = msg.date.strftime("%H:%M")
            sender = msg.sender_name or "-"
            text = (msg.text or "").replace("\n", " ").strip() or "(내용 없음)"
            suffix = ""
            if msg.media and msg.has_downloadable_media():
                suffix = f" [첨부: {msg.media.file_name}]"
            lines.append(f"- {time} {sender}: {text}{suffix}")
        lines.append("")

    lines.extend(["## 논의 / 결정 사항", "채팅 원문 기준으로 수동 정리 필요", ""])
    lines.extend(["## 요청 / 협업", "채팅 원문 기준으로 수동 정리 필요", ""])
    lines.extend(["## 이슈 및 리스크", "없음", "", "## 산출물 / 첨부"])
    attachments = [
        media
        for chat in bundle.chats
        for media in chat.attachments
    ]
    if attachments:
        for media in attachments:
            location = media.local_path or "(미다운로드)"
            lines.append(f"- {media.file_name or media.type}: {location}")
    else:
        lines.append("없음")
    lines.extend(["", "## 다음 액션", "없음", ""])
    return "\n".join(lines)


async def generate_journal(
    bundle: DailyBundle,
    config: AppConfig,
    *,
    model: str | None = None,
    scope: str = "combined",
) -> str:
    from worklog_agent.journal_prompt import resolve_system_prompt

    payload = bundle.to_prompt_payload()
    if scope == "room" and len(bundle.chats) == 1:
        title = bundle.chats[0].title
        user_prompt = (
            f"날짜: {bundle.date} ({bundle.timezone})\n"
            f"채팅방: {title}\n"
            "아래 JSON은 해당 채팅방의 하루치 업무 채팅입니다. "
            "이 방만의 한국어 업무 일지로 정리하세요. "
            "다른 방은 없다고 가정하세요.\n\n"
            f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```"
        )
    else:
        user_prompt = (
            f"날짜: {bundle.date} ({bundle.timezone})\n"
            "아래 JSON은 하루치 업무 채팅입니다. 한국어 업무 일지로 정리하세요.\n\n"
            f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```"
        )
    system = resolve_system_prompt(config.data_root, bundle.date)
    content = await ollama_chat(config, system, user_prompt, model=model)
    return content.strip() + "\n"
