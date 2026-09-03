from unittest.mock import AsyncMock, patch

import pytest

from worklog_agent.config import AppConfig
from worklog_agent.journal import generate_journal
from worklog_agent.models import DailyBundle, DailyChat, MessageRecord
from worklog_agent.ollama import OllamaError, _clean_content, _model_installed, chat
from datetime import datetime, timezone


def test_clean_think_tags() -> None:
    raw = "<think>내부 추론</think>\n# 업무 일지\n요약"
    assert _clean_content(raw) == "# 업무 일지\n요약"


def test_model_installed_matches_tag() -> None:
    installed = ["qwen2.5:7b", "qwen3:14b"]
    assert _model_installed("qwen2.5:7b", installed) == "qwen2.5:7b"
    assert _model_installed("qwen3", installed) == "qwen3:14b"
    assert _model_installed("llama3", installed) is None


@pytest.mark.asyncio
async def test_chat_uses_ollama_native_api() -> None:
    config = AppConfig()
    tags = {"models": [{"name": "gemma4:e4b"}]}
    reply = {"message": {"content": "# 업무 일지\n완료"}}

    class FakeResponse:
        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._payload

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url: str):
            assert url.endswith("/api/tags")
            return FakeResponse(tags)

        async def post(self, url: str, json: dict):
            assert url.endswith("/api/chat")
            assert json["model"] == "gemma4:e4b"
            assert json["stream"] is False
            return FakeResponse(reply)

    with patch("worklog_agent.ollama.httpx.AsyncClient", FakeClient):
        text = await chat(config, "system", "user")
    assert text == "# 업무 일지\n완료"


@pytest.mark.asyncio
async def test_generate_journal_calls_ollama() -> None:
    bundle = DailyBundle(
        date="2026-09-03",
        timezone="Asia/Seoul",
        chats=[
            DailyChat(
                chat_id=-1001,
                title="팀 업무방",
                message_count=1,
                participants=["김수민"],
                messages=[
                    MessageRecord(
                        id=1,
                        chat_id=-1001,
                        chat_title="팀 업무방",
                        date=datetime(2026, 9, 3, 9, 0, tzinfo=timezone.utc),
                        sender_name="김수민",
                        text="배포 완료",
                    )
                ],
            )
        ],
        totals={"messages": 1, "attachments": 0, "chats": 1},
    )
    with patch("worklog_agent.journal.ollama_chat", new=AsyncMock(return_value="# 일지")):
        markdown = await generate_journal(bundle, AppConfig())
    assert markdown == "# 일지\n"


@pytest.mark.asyncio
async def test_chat_errors_when_ollama_down() -> None:
    import httpx

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url: str):
            raise httpx.ConnectError("connection refused")

    with patch("worklog_agent.ollama.httpx.AsyncClient", FakeClient):
        with pytest.raises(OllamaError, match="Ollama에 연결하지 못했습니다"):
            await chat(AppConfig(), "system", "user")
