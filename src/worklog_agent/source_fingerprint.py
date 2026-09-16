from __future__ import annotations

import hashlib
from datetime import date

from worklog_agent.models import MessageRecord
from worklog_agent.organize import to_local_date
from worklog_agent.storage import Storage


def _message_signature(record: MessageRecord) -> str:
    media = ""
    if record.media is not None:
        media = f"{record.media.type}:{record.media.file_name or ''}:{record.media.size or 0}"
    return f"{record.chat_id}:{record.id}:{len(record.text)}:{media}"


def day_source_fingerprint(storage: Storage, day: str, tz_name: str) -> str:
    target = date.fromisoformat(day)
    signatures: list[str] = []
    for chat_id in storage.iter_chat_ids():
        for record in storage.read_messages(chat_id):
            if to_local_date(record.date, tz_name) != target:
                continue
            signatures.append(_message_signature(record))
    if signatures:
        payload = "\n".join(sorted(signatures))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    daily = storage.daily_path(day)
    if daily.is_file():
        return hashlib.sha256(daily.read_bytes()).hexdigest()[:16]
    return "empty"
