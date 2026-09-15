from __future__ import annotations

import logging

from worklog_agent.archive import archive_pending
from worklog_agent.collect import collect_all
from worklog_agent.config import AppConfig
from worklog_agent.journal import generate_journal
from worklog_agent.organize import organize_day, target_day
from worklog_agent.storage import Storage

logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.storage = Storage(config.data_root)
        self.storage.ensure()

    async def collect(self, day: str | None = None) -> int:
        return await collect_all(self.config, self.storage, day=day)

    async def archive(self) -> int:
        return await archive_pending(self.config, self.storage)

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

    async def journal(self, day: str | None = None) -> str:
        resolved = target_day(day, self.config.timezone)
        bundle = self.organize(resolved)
        markdown = await generate_journal(bundle, self.config)
        path = self.storage.save_journal(resolved, markdown)
        logger.info("일지 저장: %s", path)
        return str(path)

    async def run(self, day: str | None = None) -> str:
        resolved = target_day(day, self.config.timezone)
        collected = await self.collect(resolved)
        archived = await self.archive()
        path = await self.journal(resolved)
        logger.info("파이프라인 완료: 수집 %s, 첨부 %s, 일지 %s", collected, archived, path)
        return path
