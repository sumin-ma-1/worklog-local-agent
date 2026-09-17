from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Callable

import httpx

from worklog_agent.config import AppConfig
from worklog_agent.users import (
    consume_bot_link_token,
    find_telegram_link_owner,
    is_bot_linked,
    load_user_preferences,
    mark_bot_linked,
    resolve_account_by_telegram_id,
    user_config,
)
from worklog_agent.web.journal_ask import answer_journal_question
from worklog_agent.web.run_service import (
    EmptyPlanError,
    RunBusyError,
    enqueue_planned_run,
)

logger = logging.getLogger(__name__)

RuntimeFactory = Callable[[str], Any]


class TelegramBotError(RuntimeError):
    pass


class TelegramBotClient:
    def __init__(self, token: str, *, timeout: float = 60.0) -> None:
        self.token = token.strip()
        self.base = f"https://api.telegram.org/bot{self.token}"
        self.timeout = timeout

    async def _call(self, method: str, payload: dict | None = None) -> dict:
        url = f"{self.base}/{method}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(url, json=payload or {})
            response.raise_for_status()
            data = response.json()
        if not data.get("ok"):
            raise TelegramBotError(str(data.get("description") or data))
        return data.get("result")

    async def get_updates(self, offset: int | None = None, timeout: int = 25) -> list[dict]:
        payload: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        result = await self._call("getUpdates", payload)
        return list(result or [])

    async def send_message(
        self,
        chat_id: int | str,
        text: str,
        *,
        reply_markup: dict | None = None,
    ) -> dict:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text[:4000],
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return await self._call("sendMessage", payload)

    async def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> dict:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text[:200]
        return await self._call("answerCallbackQuery", payload)


def _generate_keyboard(dates: list[str], label: str | None = None) -> dict:
    day = dates[0]
    title = f"{label or day} 일지 생성"
    return {
        "inline_keyboard": [[{"text": title, "callback_data": f"gen:{day}"}]],
    }


class TelegramBotWorker:
    def __init__(
        self,
        config: AppConfig,
        runtime_for: RuntimeFactory,
        *,
        stop_event: threading.Event | None = None,
    ) -> None:
        self.config = config
        self.runtime_for = runtime_for
        self.stop_event = stop_event or threading.Event()
        token = str(config.env.telegram_bot_token or "").strip()
        self.client = TelegramBotClient(token) if token else None
        self._offset: int | None = None

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def run_forever(self) -> None:
        if not self.client:
            logger.info("TELEGRAM_BOT_TOKEN 없음 — 봇 폴링을 건너뜁니다.")
            return
        logger.info("텔레그램 봇 long polling 시작")
        while not self.stop_event.is_set():
            try:
                asyncio.run(self._poll_once())
            except Exception:
                logger.exception("텔레그램 봇 폴링 실패")
                self.stop_event.wait(3)

    async def _poll_once(self) -> None:
        assert self.client is not None
        updates = await self.client.get_updates(offset=self._offset, timeout=25)
        for update in updates:
            update_id = int(update.get("update_id") or 0)
            self._offset = update_id + 1
            try:
                await self.handle_update(update)
            except Exception:
                logger.exception("텔레그램 업데이트 처리 실패: %s", update_id)

    async def handle_update(self, update: dict) -> None:
        if update.get("callback_query"):
            await self._handle_callback(update["callback_query"])
            return
        message = update.get("message") or update.get("edited_message")
        if not message:
            return
        chat = message.get("chat") or {}
        if chat.get("type") != "private":
            return
        text = str(message.get("text") or "").strip()
        if not text:
            return
        from_user = message.get("from") or {}
        tg_id = from_user.get("id")
        chat_id = chat.get("id")
        if tg_id is None or chat_id is None:
            return
        if text.startswith("/start"):
            await self._handle_start(chat_id=chat_id, tg_id=tg_id, text=text)
            return
        await self._handle_text(chat_id=chat_id, tg_id=tg_id, text=text)

    async def _handle_start(self, *, chat_id: int, tg_id: int, text: str) -> None:
        assert self.client is not None
        parts = text.split(maxsplit=1)
        token = parts[1].strip() if len(parts) > 1 else ""
        data_root = self.config.data_root

        if token:
            user_id = consume_bot_link_token(data_root, token)
            if not user_id:
                await self.client.send_message(
                    chat_id,
                    "연결 링크가 만료되었거나 올바르지 않습니다. 대시보드에서 봇 바로가기를 다시 열어 주세요.",
                )
                return
            owner = find_telegram_link_owner(
                data_root,
                self.config.telegram.session_name,
                telegram_user_id=tg_id,
                exclude_user_id=user_id,
            )
            if owner:
                await self.client.send_message(
                    chat_id,
                    "이 텔레그램 계정은 다른 대시보드 사용자에 이미 연결되어 있습니다.",
                )
                return
            from worklog_agent.users import ensure_user_root, load_user_telegram

            root = ensure_user_root(data_root, user_id)
            existing = str(load_user_telegram(root).get("telegram_user_id") or "").strip()
            if existing and existing != str(tg_id):
                await self.client.send_message(
                    chat_id,
                    "이 대시보드 계정은 다른 텔레그램 계정과 이미 연동되어 있습니다.",
                )
                return
            mark_bot_linked(data_root, user_id, telegram_user_id=tg_id, chat_id=chat_id)
            await self.client.send_message(
                chat_id,
                "일지 비서와 연결되었습니다. 일지를 묻거나 「어제 일지 생성해줘」라고 말해 보세요.",
            )
            return
        user_id = resolve_account_by_telegram_id(data_root, tg_id)
        if user_id:
            mark_bot_linked(data_root, user_id, telegram_user_id=tg_id, chat_id=chat_id)
            await self.client.send_message(
                chat_id,
                "다시 오신 것을 환영합니다. 일지를 묻거나 오늘/어제 일지 생성을 요청해 보세요.",
            )
            return

        await self.client.send_message(
            chat_id,
            "아직 대시보드 계정과 연결되지 않았습니다.\n"
            "1) 대시보드에서 텔레그램(수집) 연동\n"
            "2) 일지 화면 챗봇 헤더의 텔레그램 바로가기로 연결\n"
            "을 진행해 주세요.",
        )

    async def _handle_text(self, *, chat_id: int, tg_id: int, text: str) -> None:
        assert self.client is not None
        user_id = resolve_account_by_telegram_id(self.config.data_root, tg_id)
        if not user_id:
            await self.client.send_message(
                chat_id,
                "연결된 대시보드 계정이 없습니다. 대시보드 챗 헤더의 봇 바로가기로 먼저 연결해 주세요.",
            )
            return
        cfg = user_config(self.config, user_id)
        if not is_bot_linked(cfg.data_root):
            mark_bot_linked(
                self.config.data_root,
                user_id,
                telegram_user_id=tg_id,
                chat_id=chat_id,
            )
        try:
            result = await answer_journal_question(cfg, text)
        except Exception as exc:  # noqa: BLE001
            logger.exception("봇 일지 질의 실패")
            await self.client.send_message(chat_id, f"질의에 실패했습니다: {exc}")
            return

        if result.get("intent") == "generate" and result.get("generate"):
            generate = result["generate"]
            dates = list(generate.get("dates") or [])
            label = generate.get("label")
            await self.client.send_message(
                chat_id,
                result.get("answer") or "일지를 생성할까요?",
                reply_markup=_generate_keyboard(dates, label),
            )
            return

        answer = str(result.get("answer") or "")
        days = list(result.get("days") or [])
        if days:
            answer = f"{answer}\n\n관련 날짜: {days[0]}"
        await self.client.send_message(chat_id, answer)

    async def _handle_callback(self, query: dict) -> None:
        assert self.client is not None
        callback_id = str(query.get("id") or "")
        data = str(query.get("data") or "")
        message = query.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        from_user = query.get("from") or {}
        tg_id = from_user.get("id")
        if chat_id is None or tg_id is None:
            return
        if not data.startswith("gen:"):
            await self.client.answer_callback_query(callback_id)
            return
        day = data.split(":", 1)[1].strip()
        user_id = resolve_account_by_telegram_id(self.config.data_root, tg_id)
        if not user_id:
            await self.client.answer_callback_query(callback_id, "계정 연결이 필요합니다.")
            return
        await self.client.answer_callback_query(callback_id, "생성을 시작합니다…")
        cfg = user_config(self.config, user_id)
        prefs = load_user_preferences(cfg.data_root)
        generate_type = str(prefs.get("generate_type") or "combined")
        runtime = self.runtime_for(user_id)
        body = {
            "dates": [day],
            "skip_existing": True,
            "regenerate_if_stale": False,
            "force": False,
            "generate_type": generate_type,
        }
        try:
            enqueue_planned_run(self.config, user_id, cfg, runtime, body)
        except RunBusyError:
            await self.client.send_message(chat_id, "이미 일지 생성이 진행 중입니다. 잠시 후 다시 시도해 주세요.")
            return
        except EmptyPlanError:
            await self.client.send_message(chat_id, f"{day} 일지가 이미 있어 건너뜁니다.")
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("봇 일지 생성 실패")
            await self.client.send_message(chat_id, f"생성 요청 실패: {exc}")
            return
        await self.client.send_message(
            chat_id,
            f"{day} 일지 생성을 시작했습니다. 완료되면 대시보드에서 확인할 수 있습니다.",
        )


def start_telegram_bot_thread(
    config: AppConfig,
    runtime_for: RuntimeFactory,
    stop_event: threading.Event,
) -> threading.Thread | None:
    token = str(config.env.telegram_bot_token or "").strip()
    if not token:
        return None
    worker = TelegramBotWorker(config, runtime_for, stop_event=stop_event)
    thread = threading.Thread(target=worker.run_forever, daemon=True, name="worklog-telegram-bot")
    thread.start()
    return thread
