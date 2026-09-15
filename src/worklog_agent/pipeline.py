from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from worklog_agent.archive import archive_pending
from worklog_agent.collect import collect_all
from worklog_agent.config import AppConfig
from worklog_agent.journal import generate_journal
from worklog_agent.ollama import (
    _model_already_loaded,
    list_running_model_names,
    warmup_model,
)
from worklog_agent.organize import organize_day, target_day
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
        bundle = organize_day(self.storage, resolved, self.config.timezone)
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
        on_progress: ProgressFn | None = None,
    ) -> str:
        resolved = target_day(day, self.config.timezone)
        await emit_progress(on_progress, f"{resolved} · 날짜별 정리 중", "organize")
        bundle = self.organize(resolved)
        model_name = self.config.journal.ollama.model
        running = await list_running_model_names(self.config)
        if _model_already_loaded(model_name, running):
            await emit_progress(on_progress, f"모델 확인: {model_name} (이미 로드됨)", "model")
        else:
            await emit_progress(on_progress, f"모델 로드 중: {model_name}", "model")
        model = await warmup_model(self.config)
        await emit_progress(on_progress, f"{resolved} · 일지 생성 중 ({model})", "journal")
        markdown = await generate_journal(bundle, self.config, model=model)
        path = self.storage.save_journal(resolved, markdown)
        logger.info("일지 저장: %s", path)
        return str(path)

    async def run(
        self,
        day: str | None = None,
        *,
        on_progress: ProgressFn | None = None,
    ) -> str:
        resolved = target_day(day, self.config.timezone)
        await emit_progress(on_progress, f"{resolved} · 메시지 수집 시작", "collect")
        collected = await self.collect(resolved, on_progress=on_progress)
        await emit_progress(on_progress, f"{resolved} · 첨부 저장 중", "archive")
        archived = await self.archive(on_progress=on_progress)
        path = await self.journal(resolved, on_progress=on_progress)
        logger.info("파이프라인 완료: 수집 %s, 첨부 %s, 일지 %s", collected, archived, path)
        return path
