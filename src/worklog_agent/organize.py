from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from worklog_agent.models import DailyBundle, DailyChat, MessageRecord
from worklog_agent.storage import Storage


def to_local_date(dt: datetime, tz_name: str) -> date:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo(tz_name)).date()


def target_day(day: str | None, tz_name: str) -> str:
    if day:
        return date.fromisoformat(day).isoformat()
    return datetime.now(ZoneInfo(tz_name)).date().isoformat()


def build_daily_bundle(
    messages: list[MessageRecord],
    day: str,
    tz_name: str,
) -> DailyBundle:
    target = date.fromisoformat(day)
    grouped: dict[int, list[MessageRecord]] = defaultdict(list)
    titles: dict[int, str] = {}

    for record in messages:
        if to_local_date(record.date, tz_name) != target:
            continue
        grouped[record.chat_id].append(record)
        titles[record.chat_id] = record.chat_title

    chats: list[DailyChat] = []
    total_messages = 0
    total_attachments = 0
    for chat_id, records in sorted(grouped.items(), key=lambda item: titles.get(item[0], "")):
        records.sort(key=lambda item: item.date)
        participants = []
        seen: set[str] = set()
        for record in records:
            name = record.sender_name or (str(record.sender_id) if record.sender_id else "unknown")
            if name not in seen:
                seen.add(name)
                participants.append(name)
        attachments = [
            record.media for record in records if record.media and record.has_downloadable_media()
        ]
        chats.append(
            DailyChat(
                chat_id=chat_id,
                title=titles[chat_id],
                message_count=len(records),
                participants=participants,
                messages=records,
                attachments=attachments,
            )
        )
        total_messages += len(records)
        total_attachments += len(attachments)

    return DailyBundle(
        date=target.isoformat(),
        timezone=tz_name,
        chats=chats,
        totals={"messages": total_messages, "attachments": total_attachments, "chats": len(chats)},
    )


def organize_day(storage: Storage, day: str, tz_name: str) -> DailyBundle:
    bundle = build_daily_bundle(storage.load_all_messages(), day, tz_name)
    storage.save_daily(bundle)
    return bundle
