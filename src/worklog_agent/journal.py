from __future__ import annotations

import json

from worklog_agent.config import AppConfig
from worklog_agent.journal_prompt import default_system_prompt
from worklog_agent.models import DailyBundle
from worklog_agent.ollama import chat as ollama_chat

JOURNAL_SYSTEM_PROMPT = default_system_prompt()


def render_draft(bundle: DailyBundle) -> str:
    lines = [
        f"# 업무 일지 ({bundle.date})",
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
) -> str:
    from worklog_agent.journal_prompt import resolve_system_prompt

    payload = bundle.to_prompt_payload()
    user_prompt = (
        f"날짜: {bundle.date} ({bundle.timezone})\n"
        "아래 JSON은 하루치 업무 채팅입니다. 한국어 업무 일지로 정리하세요.\n\n"
        f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```"
    )
    system = resolve_system_prompt(config.data_root, bundle.date)
    content = await ollama_chat(config, system, user_prompt, model=model)
    return content.strip() + "\n"
