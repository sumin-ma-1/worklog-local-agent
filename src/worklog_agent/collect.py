from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from telethon import TelegramClient, utils
from telethon.tl.custom.message import Message
from telethon.tl.types import Channel, Chat, User

from worklog_agent.config import AppConfig
from worklog_agent.models import MediaRef, MessageRecord
from worklog_agent.storage import Storage, utcnow

logger = logging.getLogger(__name__)

DOWNLOADABLE_MEDIA = {"photo", "document", "video", "audio", "voice", "gif"}


def require_telegram_credentials(config: AppConfig) -> tuple[int, str]:
    api_id = config.env.telegram_api_id
    api_hash = config.env.telegram_api_hash
    if not api_id or not api_hash:
        raise RuntimeError(
            "TELEGRAM_API_ID / TELEGRAM_API_HASH 가 필요합니다. "
            "대시보드 설정 탭 또는 .env 를 확인하세요. (https://my.telegram.org)"
        )
    return api_id, api_hash


def build_client(config: AppConfig, storage: Storage) -> TelegramClient:
    api_id, api_hash = require_telegram_credentials(config)
    session = str(storage.session_path(config.telegram.session_name))
    return TelegramClient(session, api_id, api_hash)


async def ensure_authorized(
    client: TelegramClient,
    config: AppConfig,
    *,
    interactive: bool = True,
) -> None:
    await client.connect()
    if not await client.is_user_authorized():
        if not interactive:
            raise RuntimeError(
                "텔레그램 로그인이 완료되지 않았습니다. "
                "대시보드 설정 탭에서 로그인하거나 `worklog-agent auth` 를 실행하세요."
            )
        await client.start(phone=config.env.telegram_phone)
    if not await client.is_user_authorized():
        raise RuntimeError(
            "텔레그램 로그인이 완료되지 않았습니다. "
            "대시보드 설정 탭에서 로그인하거나 `worklog-agent auth` 를 실행하세요."
        )


async def load_dialogs(config: AppConfig, *, interactive: bool = False) -> list[dict[str, str | int]]:
    storage = Storage(config.data_root)
    storage.ensure()
    client = build_client(config, storage)
    async with client:
        await ensure_authorized(client, config, interactive=interactive)
        return await list_dialogs(client)


async def list_dialogs(client: TelegramClient) -> list[dict[str, str | int]]:
    rows: list[dict[str, str | int]] = []
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        rows.append(
            {
                "id": dialog.id,
                "title": dialog.name or "",
                "type": _entity_type(entity),
            }
        )
    return rows


async def resolve_chat(client: TelegramClient, spec: str | int):
    if isinstance(spec, int) or (isinstance(spec, str) and spec.lstrip("-").isdigit()):
        return await client.get_entity(int(spec))

    text = str(spec).strip()
    try:
        return await client.get_entity(text)
    except Exception:
        logger.debug("entity 직접 조회 실패, 대화 목록에서 검색: %s", text)

    lowered = text.lower()
    async for dialog in client.iter_dialogs():
        name = dialog.name or ""
        if name == text or lowered in name.lower():
            return dialog.entity
    raise ValueError(f"채팅방을 찾지 못했습니다: {spec}")


def _entity_type(entity: object) -> str:
    if isinstance(entity, User):
        return "user"
    if isinstance(entity, Channel):
        return "channel" if entity.broadcast else "supergroup"
    if isinstance(entity, Chat):
        return "group"
    return type(entity).__name__


def chat_title(entity: object) -> str:
    title = getattr(entity, "title", None)
    if title:
        return str(title)
    first = getattr(entity, "first_name", None) or ""
    last = getattr(entity, "last_name", None) or ""
    name = f"{first} {last}".strip()
    if name:
        return name
    username = getattr(entity, "username", None)
    return str(username or getattr(entity, "id", "unknown"))


def _media_type(message: Message) -> str | None:
    if message.sticker:
        return "sticker"
    if message.gif:
        return "gif"
    if message.voice:
        return "voice"
    if message.audio:
        return "audio"
    if message.video:
        return "video"
    if message.photo:
        return "photo"
    if message.document:
        return "document"
    if message.web_preview:
        return "webpage"
    if message.media:
        return type(message.media).__name__
    return None


def _document_name(message: Message) -> str | None:
    document = message.document
    if document is None:
        return None
    for attr in document.attributes:
        name = getattr(attr, "file_name", None)
        if name:
            return str(name)
    return None


def extract_media(message: Message) -> MediaRef | None:
    media_type = _media_type(message)
    if media_type is None:
        return None

    file_name = _document_name(message)
    if media_type == "photo" and not file_name:
        file_name = f"{message.id}.jpg"
    elif not file_name:
        file_name = f"{message.id}"

    mime_type = None
    size = None
    if message.document is not None:
        mime_type = message.document.mime_type
        size = message.document.size
    return MediaRef(type=media_type, file_name=file_name, mime_type=mime_type, size=size)


async def message_to_record(
    message: Message,
    entity: object,
    sender_cache: dict[int, str],
    chat_id: int,
) -> MessageRecord:
    sender_id = message.sender_id
    sender_name = None
    if sender_id:
        if sender_id not in sender_cache:
            sender = await message.get_sender()
            sender_cache[sender_id] = chat_title(sender) if sender else str(sender_id)
        sender_name = sender_cache[sender_id]

    date = message.date
    if date is not None and date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)

    return MessageRecord(
        id=message.id,
        chat_id=chat_id,
        chat_title=chat_title(entity),
        date=date or utcnow(),
        sender_id=sender_id,
        sender_name=sender_name,
        text=message.message or "",
        reply_to_msg_id=message.reply_to_msg_id,
        media=extract_media(message),
    )


async def collect_chat(
    client: TelegramClient,
    entity: object,
    storage: Storage,
    *,
    lookback_days: int,
    skip_service: bool,
) -> int:
    title = chat_title(entity)
    chat_id = int(utils.get_peer_id(entity))
    state = storage.load_state(chat_id, title)
    if state.title != title:
        state.title = title

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    sender_cache: dict[int, str] = {}
    collected: list[MessageRecord] = []

    async for message in client.iter_messages(entity):
        if message.id <= state.last_id:
            break
        msg_date = message.date
        if msg_date is not None and msg_date.tzinfo is None:
            msg_date = msg_date.replace(tzinfo=timezone.utc)
        if state.last_id == 0 and msg_date is not None and msg_date < cutoff:
            break
        if skip_service and message.action is not None and not message.message and not message.media:
            continue
        collected.append(await message_to_record(message, entity, sender_cache, chat_id))

    collected.sort(key=lambda item: item.id)
    if collected:
        storage.append_messages(chat_id, collected)
        state.last_id = max(state.last_id, collected[-1].id)
        state.last_collected_at = utcnow()
        storage.save_state(state)

    logger.info("%s: 새 메시지 %s개", title, len(collected))
    return len(collected)


async def collect_all(config: AppConfig, storage: Storage) -> int:
    if not config.telegram.chats:
        raise RuntimeError("config.yaml 의 telegram.chats 에 채팅방을 지정하세요. `worklog-agent chats` 로 목록을 확인할 수 있습니다.")

    storage.ensure()
    total = 0
    client = build_client(config, storage)
    async with client:
        await ensure_authorized(client, config)
        for spec in config.telegram.chats:
            entity = await resolve_chat(client, spec)
            total += await collect_chat(
                client,
                entity,
                storage,
                lookback_days=config.collect.lookback_days,
                skip_service=config.collect.skip_service_messages,
            )
    return total
