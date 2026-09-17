from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from worklog_agent.config import AppConfig, StorageConfig
from worklog_agent.telegram_bot import TelegramBotWorker
from worklog_agent.users import (
    consume_bot_link_token,
    create_bot_link_token,
    ensure_user_root,
    mark_bot_linked,
    resolve_account_by_telegram_id,
    save_user_telegram,
)
from worklog_agent.web.chat_intent import telegram_bot_url
from worklog_agent.web.run_service import UserRuntime


def test_telegram_bot_url_with_start() -> None:
    assert telegram_bot_url("WorklogBot", start="abc123") == "https://t.me/WorklogBot?start=abc123"
    assert telegram_bot_url("@WorklogBot") == "https://t.me/WorklogBot"


def test_bot_link_token_roundtrip(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    token = create_bot_link_token(data_root, "user-a", ttl_seconds=600)
    assert consume_bot_link_token(data_root, token) == "user-a"
    assert consume_bot_link_token(data_root, token) is None


def test_resolve_account_by_telegram_id(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    root = ensure_user_root(data_root, "u1")
    save_user_telegram(root, {"telegram_user_id": 42, "name": "A"})
    assert resolve_account_by_telegram_id(data_root, 42) == "u1"
    assert resolve_account_by_telegram_id(data_root, 99) is None


def test_mark_bot_linked(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    meta = mark_bot_linked(data_root, "u1", telegram_user_id=7, chat_id=7)
    assert meta["telegram_user_id"] == 7
    assert meta["bot_chat_id"] == 7
    assert meta.get("bot_linked_at")


@pytest.mark.asyncio
async def test_bot_start_with_token_binds_user(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    ensure_user_root(data_root, "u1")
    token = create_bot_link_token(data_root, "u1")
    config = AppConfig(storage=StorageConfig(root=str(data_root)))
    worker = TelegramBotWorker(config, lambda _uid: UserRuntime())
    worker.client = MagicMock()
    worker.client.send_message = AsyncMock(return_value={})

    await worker.handle_update(
        {
            "update_id": 1,
            "message": {
                "chat": {"id": 42, "type": "private"},
                "from": {"id": 42},
                "text": f"/start {token}",
            },
        }
    )
    assert resolve_account_by_telegram_id(data_root, 42) == "u1"
    worker.client.send_message.assert_awaited()
    assert "연결" in worker.client.send_message.await_args.args[1]


@pytest.mark.asyncio
async def test_bot_text_routes_to_ask(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    mark_bot_linked(data_root, "u1", telegram_user_id=99, chat_id=99)
    config = AppConfig(storage=StorageConfig(root=str(data_root)))
    worker = TelegramBotWorker(config, lambda _uid: UserRuntime())
    worker.client = MagicMock()
    worker.client.send_message = AsyncMock(return_value={})

    with patch(
        "worklog_agent.telegram_bot.answer_journal_question",
        new=AsyncMock(
            return_value={
                "intent": "search",
                "answer": "안녕하세요!",
                "days": [],
                "generate": None,
            }
        ),
    ):
        await worker.handle_update(
            {
                "update_id": 2,
                "message": {
                    "chat": {"id": 99, "type": "private"},
                    "from": {"id": 99},
                    "text": "안녕",
                },
            }
        )
    worker.client.send_message.assert_awaited_with(99, "안녕하세요!")


@pytest.mark.asyncio
async def test_bot_generate_sends_keyboard(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    mark_bot_linked(data_root, "u1", telegram_user_id=5, chat_id=5)
    config = AppConfig(storage=StorageConfig(root=str(data_root)))
    worker = TelegramBotWorker(config, lambda _uid: UserRuntime())
    worker.client = MagicMock()
    worker.client.send_message = AsyncMock(return_value={})

    with patch(
        "worklog_agent.telegram_bot.answer_journal_question",
        new=AsyncMock(
            return_value={
                "intent": "generate",
                "answer": "어제 생성할까요?",
                "days": [],
                "generate": {"dates": ["2026-09-16"], "label": "어제"},
            }
        ),
    ):
        await worker.handle_update(
            {
                "update_id": 3,
                "message": {
                    "chat": {"id": 5, "type": "private"},
                    "from": {"id": 5},
                    "text": "어제 일지 생성해줘",
                },
            }
        )
    kwargs = worker.client.send_message.await_args.kwargs
    assert "reply_markup" in kwargs
    assert kwargs["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "gen:2026-09-16"


@pytest.mark.asyncio
async def test_bot_dashboard_keyboard_sends_link(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    mark_bot_linked(data_root, "u1", telegram_user_id=3, chat_id=3)
    config = AppConfig(storage=StorageConfig(root=str(data_root)))
    worker = TelegramBotWorker(config, lambda _uid: UserRuntime())
    worker.client = MagicMock()
    worker.client.send_message = AsyncMock(return_value={})

    await worker.handle_update(
        {
            "update_id": 4,
            "message": {
                "chat": {"id": 3, "type": "private"},
                "from": {"id": 3},
                "text": "대시보드",
            },
        }
    )
    assert worker.client.send_message.await_args.args[1].startswith("대시보드:")
