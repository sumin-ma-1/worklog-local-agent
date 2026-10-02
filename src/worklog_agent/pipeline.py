from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from worklog_agent.archive import archive_pending
from worklog_agent.collect import collect_all
from worklog_agent.config import AppConfig, numeric_chat_ids
from worklog_agent.journal import bundle_for_chat, generate_journal, normalize_generate_type
from worklog_agent.ollama import (
    _model_already_loaded,
    list_running_model_names,
    warmup_model,
)
from worklog_agent.organize import organize_day, target_day
from worklog_agent.journal_meta import write_journal_meta
from worklog_agent.source_fingerprint import day_source_fingerprint
from worklog_agent.storage import Storage

logger = logging.getLogger(__name__)

ProgressFn = Callable[[str, str | None], object]


async def emit_progress(
    on_progress: ProgressFn | None,
    message: str,
    step: str | None = None,
) -> None:
    if on_progress:
        result = on_progress(message, step)
        if asyncio.iscoroutine(result):
            await result
    # 폴링 요청이 끼어들 수 있게 이벤트 루프를 양보합니다.
    await asyncio.sleep(0)


class Pipeline:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.storage = Storage(config.data_root)
        self.storage.ensure()

    async def collect(
        self,
        day: str | None = None,
        *,
        on_progress: ProgressFn | None = None,
    ) -> int:
        return await collect_all(self.config, self.storage, day=day, on_progress=on_progress)

    async def archive(self, *, on_progress: ProgressFn | None = None) -> int:
        return await archive_pending(self.config, self.storage, on_progress=on_progress)

    def organize(self, day: str | None = None):
        resolved = target_day(day, self.config.timezone)
        watched = numeric_chat_ids(self.config.telegram.chats)
        bundle = organize_day(
            self.storage,
            resolved,
            self.config.timezone,
            chat_ids=watched if watched else None,
        )
        logger.info(
            "%s 정리: 채팅 %s, 메시지 %s, 첨부 %s",
            resolved,
            bundle.totals.get("chats", 0),
            bundle.totals.get("messages", 0),
            bundle.totals.get("attachments", 0),
        )
        return bundle

    async def journal(
        self,
        day: str | None = None,
        *,
        generate_type: str = "combined",
        fill_missing: bool = False,
        append_rooms: bool = False,
        on_progress: ProgressFn | None = None,
    ) -> str:
        resolved = target_day(day, self.config.timezone)
        kind = normalize_generate_type(generate_type)
        await emit_progress(on_progress, f"{resolved} · 날짜별 정리 중", "organize")
        bundle = self.organize(resolved)
        watched = numeric_chat_ids(self.config.telegram.chats)
        model_name = self.config.journal.ollama.model
        running = await list_running_model_names(self.config)
        if _model_already_loaded(model_name, running):
            await emit_progress(on_progress, f"모델 확인: {model_name} (이미 로드됨)", "model")
        else:
            await emit_progress(on_progress, f"모델 로드 중: {model_name}", "model")
        model = await warmup_model(self.config)

        want_combined = kind in {"combined", "both"}
        want_rooms = kind in {"per_room", "both"}
        existing_combined = self.storage.journal_path(resolved).exists()
        existing_room_rows = self.storage.list_room_journals(resolved)
        existing_rooms = bool(existing_room_rows)
        if fill_missing and kind == "both":
            do_combined = not existing_combined
            do_rooms = not existing_rooms
            rooms_append = False
        else:
            do_combined = want_combined
            do_rooms = want_rooms
            rooms_append = bool(append_rooms and want_rooms)

        primary_path: str | None = None
        if existing_combined:
            primary_path = str(self.storage.journal_path(resolved))

        if do_combined:
            await emit_progress(on_progress, f"{resolved} · 통합 일지 생성 중 ({model})", "journal")
            markdown = await generate_journal(bundle, self.config, model=model, scope="combined")
            primary_path = str(self.storage.save_journal(resolved, markdown))
        elif want_combined is False and existing_combined:
            # 방마다만 남기는 경우 통합본 제거
            self.storage.journal_path(resolved).unlink()
            primary_path = None

        if do_rooms:
            allowed_ids = {str(chat.chat_id) for chat in bundle.chats}
            if rooms_append:
                # Drop room journals for chats no longer in the watched set / daily bundle.
                pruned_rows: list[dict[str, str]] = []
                for item in existing_room_rows:
                    room_id = str(item["id"])
                    if room_id in allowed_ids:
                        pruned_rows.append(item)
                        continue
                    path = self.storage.room_journal_path(resolved, room_id)
                    if path.exists():
                        path.unlink(missing_ok=True)
                existing_room_rows = pruned_rows

            existing_ids = {str(item["id"]) for item in existing_room_rows} if rooms_append else set()
            if rooms_append:
                chats_to_write = [chat for chat in bundle.chats if str(chat.chat_id) not in existing_ids]
                rooms_meta = [
                    {"id": str(item["id"]), "title": str(item["title"])}
                    for item in existing_room_rows
                ]
            else:
                self.storage.clear_room_journals(resolved)
                chats_to_write = list(bundle.chats)
                rooms_meta = []

            total_rooms = len(chats_to_write) or 0
            for index, chat in enumerate(chats_to_write, start=1):
                await emit_progress(
                    on_progress,
                    f"{resolved} · 방 일지 {index}/{total_rooms}: {chat.title} ({model})",
                    "journal",
                )
                room_bundle = bundle_for_chat(bundle, chat)
                room_md = await generate_journal(
                    room_bundle, self.config, model=model, scope="room"
                )
                path = self.storage.save_room_journal(resolved, chat.chat_id, room_md)
                rooms_meta.append({"id": str(chat.chat_id), "title": chat.title})
                if primary_path is None:
                    primary_path = str(path)
            # Keep index in sync even when nothing new was added.
            if rooms_append:
                # Refresh titles for existing rooms from current bundle when available.
                title_by_id = {str(chat.chat_id): chat.title for chat in bundle.chats}
                rooms_meta = [
                    {
                        "id": row["id"],
                        "title": title_by_id.get(row["id"], row["title"]),
                    }
                    for row in rooms_meta
                ]
                # Ensure every existing journal file stays listed.
                seen = {row["id"] for row in rooms_meta}
                for row in self.storage.list_room_journals(resolved):
                    if row["id"] not in seen:
                        rooms_meta.append({"id": row["id"], "title": row["title"]})
            if rooms_meta or not rooms_append:
                self.storage.write_room_journals_index(resolved, rooms_meta)
            elif rooms_append and existing_room_rows:
                self.storage.write_room_journals_index(
                    resolved,
                    [{"id": r["id"], "title": r["title"]} for r in existing_room_rows],
                )
            if primary_path is None and existing_room_rows:
                primary_path = str(
                    self.storage.room_journal_path(resolved, existing_room_rows[0]["id"])
                )
        elif want_rooms is False:
            self.storage.clear_room_journals(resolved)

        write_journal_meta(
            self.storage,
            resolved,
            source_fingerprint=day_source_fingerprint(
                self.storage,
                resolved,
                self.config.timezone,
                chat_ids=watched if watched else None,
            ),
            model=model,
            generate_type=kind,
        )
        if primary_path is None:
            # 방이 없는 날: 빈 통합 초안이라도 남긴다.
            empty = await generate_journal(bundle, self.config, model=model, scope="combined")
            primary_path = str(self.storage.save_journal(resolved, empty))
        logger.info("일지 저장: %s", primary_path)
        return primary_path

    async def run(
        self,
        day: str | None = None,
        *,
        generate_type: str = "combined",
        fill_missing: bool = False,
        append_rooms: bool = False,
        on_progress: ProgressFn | None = None,
    ) -> str:
        resolved = target_day(day, self.config.timezone)
        await emit_progress(on_progress, f"{resolved} · 메시지 수집 시작", "collect")
        collected = await self.collect(resolved, on_progress=on_progress)
        await emit_progress(on_progress, f"{resolved} · 첨부 저장 중", "archive")
        archived = await self.archive(on_progress=on_progress)
        path = await self.journal(
            resolved,
            generate_type=generate_type,
            fill_missing=fill_missing,
            append_rooms=append_rooms,
            on_progress=on_progress,
        )
        logger.info("파이프라인 완료: 수집 %s, 첨부 %s, 일지 %s", collected, archived, path)
        return path
