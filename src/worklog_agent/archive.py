from __future__ import annotations

import asyncio
import logging
import re
from datetime import timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from worklog_agent.collect import (
    DOWNLOADABLE_MEDIA,
    build_client,
    ensure_authorized,
    resolve_chat,
)
from worklog_agent.config import AppConfig
from worklog_agent.models import MessageRecord
from worklog_agent.storage import Storage, slugify

logger = logging.getLogger(__name__)


def _safe_filename(name: str, message_id: int) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]+", "_", name).strip() or f"{message_id}"
    if len(cleaned) > 120:
        stem, dot, suffix = cleaned.rpartition(".")
        if dot and len(suffix) <= 12:
            cleaned = f"{stem[: 120 - len(suffix) - 1]}.{suffix}"
        else:
            cleaned = cleaned[:120]
    return f"{message_id}_{cleaned}"


def _local_day(record: MessageRecord, tz_name: str) -> str:
    date = record.date
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    return date.astimezone(ZoneInfo(tz_name)).date().isoformat()


async def archive_pending(config: AppConfig, storage: Storage, *, on_progress=None) -> int:
    storage.ensure()
    pending: list[MessageRecord] = []
    for record in storage.load_all_messages():
        if record.media and record.media.type in DOWNLOADABLE_MEDIA and not record.media.archived:
            pending.append(record)
    if not pending:
        logger.info("다운로드할 첨부파일이 없습니다.")
        if on_progress:
            maybe = on_progress("첨부 없음 — 건너뜀", "archive")
            if asyncio.iscoroutine(maybe):
                await maybe
        await asyncio.sleep(0)
        return 0

    chats_by_id: dict[int, list[MessageRecord]] = {}
    for record in pending:
        chats_by_id.setdefault(record.chat_id, []).append(record)

    client = build_client(config, storage)
    saved = 0
    chat_items = list(chats_by_id.items())
    async with client:
        await ensure_authorized(client, config)
        for index, (chat_id, records) in enumerate(chat_items, start=1):
            title = records[0].chat_title
            if on_progress:
                maybe = on_progress(
                    f"첨부 저장 중: {title} ({index}/{len(chat_items)}, {len(records)}개)",
                    "archive",
                )
                if asyncio.iscoroutine(maybe):
                    await maybe
            await asyncio.sleep(0)
            entity = await _resolve_entity(client, config, chat_id, title)
            existing = {item.id: item for item in storage.read_messages(chat_id)}
            chat_saved = 0
            for record in records:
                message = await client.get_messages(entity, ids=record.id)
                if message is None:
                    logger.warning("메시지를 찾지 못했습니다: chat=%s id=%s", chat_id, record.id)
                    continue
                day = _local_day(record, config.timezone)
                dest_dir = storage.attachment_dir(day, record.chat_title)
                file_name = _safe_filename(record.media.file_name or str(record.id), record.id)
                dest = dest_dir / file_name
                path = await client.download_media(message, file=str(dest))
                if not path:
                    logger.warning("첨부 다운로드 실패: chat=%s id=%s", chat_id, record.id)
                    continue
                stored = existing[record.id]
                if stored.media is not None:
                    stored.media.archived = True
                    stored.media.local_path = str(Path(path).resolve())
                    chat_saved += 1
                    saved += 1
            storage.write_messages(chat_id, existing.values())
            logger.info("%s: 첨부 %s개 저장", title, chat_saved)

    logger.info("첨부파일 %s개 아카이브 완료", saved)
    return saved


async def _resolve_entity(client, config: AppConfig, chat_id: int, title: str):
    try:
        return await client.get_entity(chat_id)
    except Exception:
        logger.debug("chat_id로 조회 실패, 제목으로 재시도: %s", title)
    for spec in [*config.telegram.chats, title, chat_id]:
        try:
            return await resolve_chat(client, spec)
        except Exception:
            continue
    raise ValueError(f"첨부 다운로드용 채팅방을 찾지 못했습니다: {chat_id} ({title})")
