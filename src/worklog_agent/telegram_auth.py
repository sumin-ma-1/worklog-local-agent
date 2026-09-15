from __future__ import annotations

import logging
from dataclasses import dataclass

from telethon.errors import FloodWaitError, PhoneCodeExpiredError, PhoneCodeInvalidError, SessionPasswordNeededError

from worklog_agent.collect import build_client
from worklog_agent.config import AppConfig
from worklog_agent.storage import Storage

logger = logging.getLogger(__name__)


@dataclass
class LoginSession:
    phone: str | None = None
    phone_code_hash: str | None = None
    stage: str = "idle"  # idle | code | password | authorized


async def auth_status(config: AppConfig) -> dict[str, object]:
    storage = Storage(config.data_root)
    storage.ensure()
    if not config.env.telegram_api_id or not config.env.telegram_api_hash:
        return {
            "authorized": False,
            "user": None,
            "message": "API ID / Hash 가 필요합니다.",
        }
    client = build_client(config, storage)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return {
                "authorized": False,
                "user": None,
                "message": "텔레그램 로그인이 필요합니다.",
            }
        me = await client.get_me()
        name = " ".join(part for part in [me.first_name, me.last_name] if part)
        return {
            "authorized": True,
            "user": {
                "id": me.id,
                "name": name or (me.username or str(me.id)),
                "username": me.username,
                "phone": me.phone,
            },
            "message": "로그인됨",
        }
    finally:
        await client.disconnect()


async def start_login(config: AppConfig, phone: str, login: LoginSession) -> dict[str, object]:
    phone = phone.strip()
    if not phone:
        raise ValueError("전화번호를 입력하세요. 예: +821012345678")
    storage = Storage(config.data_root)
    storage.ensure()
    client = build_client(config, storage)
    try:
        await client.connect()
        if await client.is_user_authorized():
            me = await client.get_me()
            name = " ".join(part for part in [me.first_name, me.last_name] if part)
            login.stage = "authorized"
            login.phone = phone
            login.phone_code_hash = None
            return {
                "stage": "authorized",
                "message": f"이미 로그인되어 있습니다: {name or me.id}",
            }
        sent = await client.send_code_request(phone)
        login.phone = phone
        login.phone_code_hash = sent.phone_code_hash
        login.stage = "code"
        return {
            "stage": "code",
            "message": "텔레그램 앱으로 인증코드를 보냈습니다.",
        }
    except FloodWaitError as exc:
        raise RuntimeError(f"요청이 너무 많습니다. {exc.seconds}초 후 다시 시도하세요.") from exc
    finally:
        await client.disconnect()


async def submit_code(config: AppConfig, code: str, login: LoginSession) -> dict[str, object]:
    code = code.strip()
    if not code:
        raise ValueError("인증코드를 입력하세요.")
    if login.stage not in {"code", "password"} or not login.phone or not login.phone_code_hash:
        raise ValueError("먼저 전화번호로 인증코드를 요청하세요.")

    storage = Storage(config.data_root)
    storage.ensure()
    client = build_client(config, storage)
    try:
        await client.connect()
        try:
            await client.sign_in(
                phone=login.phone,
                code=code,
                phone_code_hash=login.phone_code_hash,
            )
        except SessionPasswordNeededError:
            login.stage = "password"
            return {
                "stage": "password",
                "message": "2단계 인증 비밀번호가 필요합니다.",
            }
        except PhoneCodeInvalidError as exc:
            raise ValueError("인증코드가 올바르지 않습니다.") from exc
        except PhoneCodeExpiredError as exc:
            login.stage = "idle"
            login.phone_code_hash = None
            raise ValueError("인증코드가 만료되었습니다. 다시 요청하세요.") from exc

        me = await client.get_me()
        name = " ".join(part for part in [me.first_name, me.last_name] if part)
        login.stage = "authorized"
        login.phone_code_hash = None
        return {
            "stage": "authorized",
            "message": f"로그인 완료: {name or me.id}",
            "user": {"id": me.id, "name": name or str(me.id)},
        }
    finally:
        await client.disconnect()


async def submit_password(config: AppConfig, password: str, login: LoginSession) -> dict[str, object]:
    password = password.strip()
    if not password:
        raise ValueError("2단계 인증 비밀번호를 입력하세요.")
    if login.stage != "password":
        raise ValueError("비밀번호 입력이 필요한 상태가 아닙니다.")

    storage = Storage(config.data_root)
    storage.ensure()
    client = build_client(config, storage)
    try:
        await client.connect()
        await client.sign_in(password=password)
        me = await client.get_me()
        name = " ".join(part for part in [me.first_name, me.last_name] if part)
        login.stage = "authorized"
        login.phone_code_hash = None
        return {
            "stage": "authorized",
            "message": f"로그인 완료: {name or me.id}",
            "user": {"id": me.id, "name": name or str(me.id)},
        }
    finally:
        await client.disconnect()
