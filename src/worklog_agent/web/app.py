from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from worklog_agent.accounts import AccountError, AccountStore, is_admin_username
from worklog_agent.config import (
    AppConfig,
    chat_ref_key,
    load_config,
    normalize_chat_ref,
)
from worklog_agent.pipeline import Pipeline
from worklog_agent.storage import Storage
from worklog_agent.telegram_auth import (
    LoginSession,
    auth_status,
    logout,
    start_login,
    submit_code,
    submit_password,
)
from worklog_agent.users import (
    add_user_chat,
    create_share_token,
    delete_user_data,
    ensure_user_root,
    find_share,
    load_user_telegram,
    migrate_legacy_to_user,
    remove_user_chat,
    revoke_share_for_day,
    save_user_telegram,
    share_for_day,
    telegram_linked,
    user_config,
    user_root,
)
from worklog_agent.web.auth_session import COOKIE_USER, SessionStore

logger = logging.getLogger(__name__)
WEB_DIR = Path(__file__).parent
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_COOKIE_MAX_AGE = 60 * 60 * 24 * 180


def _require_day(day: str) -> str:
    if not _DAY_RE.fullmatch(day):
        raise HTTPException(status_code=400, detail="날짜 형식이 올바르지 않습니다. YYYY-MM-DD")
    return day


@dataclass
class JobState:
    status: str = "idle"
    message: str = ""
    step: str | None = None
    date: str | None = None
    path: str | None = None


@dataclass
class UserRuntime:
    job: JobState = field(default_factory=JobState)
    job_lock: threading.Lock = field(default_factory=threading.Lock)
    worker: threading.Thread | None = None

    def set_job(self, **kwargs: object) -> dict:
        with self.job_lock:
            self.job = JobState(**kwargs)  # type: ignore[arg-type]
            return dict(self.job.__dict__)

    def snapshot_job(self) -> dict:
        with self.job_lock:
            return dict(self.job.__dict__)


@dataclass
class DashboardState:
    config_path: Path
    config: AppConfig
    sessions: SessionStore
    accounts: AccountStore
    logins: dict[str, LoginSession] = field(default_factory=dict)
    logins_lock: threading.Lock = field(default_factory=threading.Lock)
    runtimes: dict[str, UserRuntime] = field(default_factory=dict)
    runtimes_lock: threading.Lock = field(default_factory=threading.Lock)

    def reload(self) -> AppConfig:
        self.config = load_config(self.config_path)
        return self.config

    def runtime_for(self, user_id: int | str) -> UserRuntime:
        key = str(user_id)
        with self.runtimes_lock:
            if key not in self.runtimes:
                self.runtimes[key] = UserRuntime()
            return self.runtimes[key]

    def login_for(self, user_id: str) -> LoginSession:
        with self.logins_lock:
            if user_id not in self.logins:
                self.logins[user_id] = LoginSession()
            return self.logins[user_id]

    def clear_login(self, user_id: str | None) -> None:
        if not user_id:
            return
        with self.logins_lock:
            self.logins.pop(user_id, None)


class ChatBody(BaseModel):
    id: str | int
    title: str | None = None


class JournalBody(BaseModel):
    markdown: str


class RunBody(BaseModel):
    date: str | None = Field(default=None)


class AuthBody(BaseModel):
    username: str
    password: str


class LinkStartBody(BaseModel):
    phone: str


class CodeBody(BaseModel):
    code: str


class PasswordBody(BaseModel):
    password: str


def _today(config: AppConfig) -> str:
    return datetime.now(ZoneInfo(config.timezone)).date().isoformat()


def _cookie_kwargs() -> dict:
    return {
        "httponly": True,
        "samesite": "lax",
        "max_age": _COOKIE_MAX_AGE,
        "path": "/",
    }


def _set_cookie(response: Response, name: str, value: str) -> None:
    response.set_cookie(name, value, **_cookie_kwargs())


def _clear_cookie(response: Response, name: str) -> None:
    response.delete_cookie(name, path="/")


def _api_ready(config: AppConfig) -> bool:
    return bool(config.env.telegram_api_id and config.env.telegram_api_hash)


def _watched_chats(cfg: AppConfig) -> list[dict[str, object]]:
    storage = Storage(cfg.data_root)
    storage.ensure()
    titles = storage.load_chat_titles()
    rows: list[dict[str, object]] = []
    for spec in cfg.telegram.chats:
        ref = normalize_chat_ref(spec)
        key = str(ref)
        row: dict[str, object] = {
            "id": ref,
            "key": chat_ref_key(ref),
            "title": titles.get(key) or str(ref),
            "last_id": 0,
            "last_collected_at": None,
        }
        if isinstance(ref, int):
            meta = storage.watched_chat_meta(ref)
            if meta.get("title"):
                row["title"] = meta["title"]
            elif titles.get(key):
                row["title"] = titles[key]
            row["last_id"] = meta.get("last_id") or 0
            row["last_collected_at"] = meta.get("last_collected_at")
        rows.append(row)
    return rows


def _journal_payload(storage: Storage, day: str) -> dict:
    markdown = ""
    daily = None
    has_journal = storage.journal_path(day).exists()
    if has_journal:
        markdown = storage.read_journal(day)
    if storage.daily_path(day).exists():
        daily = storage.load_daily(day).model_dump(mode="json")
    return {
        "date": day,
        "markdown": markdown,
        "has_journal": has_journal,
        "daily": daily,
        "attachments": storage.list_attachments(day),
    }


def create_app(config_path: Path | None = None) -> FastAPI:
    path = Path(config_path or "config.yaml").expanduser()
    if not path.is_absolute():
        path = path.resolve()
    base = load_config(path)
    state = DashboardState(
        config_path=path,
        config=base,
        sessions=SessionStore(base.data_root),
        accounts=AccountStore(base.data_root),
    )

    app = FastAPI(title="worklog-local-agent", docs_url=None, redoc_url=None)
    app.state.dashboard = state
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))

    def current_session(request: Request) -> dict | None:
        return state.sessions.resolve(request.cookies.get(COOKIE_USER))

    def require_account(request: Request) -> tuple[str, dict]:
        session = current_session(request)
        if not session:
            raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
        user_id = str(session["user_id"])
        account = state.accounts.get(user_id)
        if not account:
            raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
        return user_id, account

    def require_telegram(request: Request) -> tuple[str, AppConfig]:
        user_id, _ = require_account(request)
        state.reload()
        cfg = user_config(state.config, user_id)
        if not telegram_linked(cfg.data_root, cfg.telegram.session_name):
            raise HTTPException(status_code=403, detail="telegram_required")
        return user_id, cfg

    def require_admin(request: Request) -> tuple[str, dict]:
        user_id, account = require_account(request)
        if not is_admin_username(str(account.get("username") or "")):
            raise HTTPException(status_code=403, detail="관리자만 접근할 수 있습니다.")
        return user_id, account

    def me_payload(user_id: str, account: dict) -> dict:
        state.reload()
        root = ensure_user_root(state.config.data_root, user_id)
        meta = load_user_telegram(root)
        linked = telegram_linked(root, state.config.telegram.session_name)
        return {
            "user": state.accounts.public_user(account),
            "is_admin": is_admin_username(str(account.get("username") or "")),
            "telegram": {
                "linked": linked,
                "phone": meta.get("phone"),
                "api_ready": _api_ready(state.config),
                "telegram_user_id": meta.get("telegram_user_id"),
                "name": meta.get("name"),
            },
        }

    async def finalize_telegram_link(user_id: str, result: dict) -> dict:
        user = result.get("user") if isinstance(result.get("user"), dict) else None
        if result.get("stage") != "authorized" or not user:
            return result
        state.reload()
        root = ensure_user_root(state.config.data_root, user_id)
        save_user_telegram(
            root,
            {
                "telegram_user_id": user.get("id"),
                "name": user.get("name"),
                "linked_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        state.clear_login(user_id)
        return {**result, "linked": True}

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        state.reload()
        return templates.TemplateResponse(
            request,
            "index.html",
            {"today": _today(state.config)},
        )

    @app.get("/s/{token}", response_class=HTMLResponse)
    async def share_page(request: Request, token: str) -> HTMLResponse:
        state.reload()
        found = find_share(state.config.data_root, token)
        if not found:
            raise HTTPException(status_code=404, detail="공유 링크가 없거나 만료되었습니다.")
        user_dir, meta = found
        day = str(meta.get("day") or "")
        if not _DAY_RE.fullmatch(day):
            raise HTTPException(status_code=404, detail="공유 링크가 올바르지 않습니다.")
        storage = Storage(user_dir)
        payload = _journal_payload(storage, day)
        attachments = [
            {
                **file,
                "href": f"/api/share/{token}/file?path={quote(str(file.get('relative') or ''), safe='')}",
            }
            for file in payload["attachments"]
        ]
        return templates.TemplateResponse(
            request,
            "share.html",
            {
                "day": day,
                "markdown": payload["markdown"],
                "markdown_json": json.dumps(payload["markdown"] or ""),
                "has_journal": payload["has_journal"],
                "attachments": attachments,
                "token": token,
            },
        )

    @app.get("/api/share/{token}")
    async def share_api(token: str) -> dict:
        state.reload()
        found = find_share(state.config.data_root, token)
        if not found:
            raise HTTPException(status_code=404, detail="공유 링크가 없거나 만료되었습니다.")
        user_dir, meta = found
        day = _require_day(str(meta.get("day") or ""))
        return _journal_payload(Storage(user_dir), day)

    @app.get("/api/share/{token}/file")
    async def share_file(token: str, path: str = Query(..., min_length=1)) -> FileResponse:
        state.reload()
        found = find_share(state.config.data_root, token)
        if not found:
            raise HTTPException(status_code=404, detail="공유 링크가 없거나 만료되었습니다.")
        user_dir, meta = found
        day = str(meta.get("day") or "")
        rel = path.replace("\\", "/").lstrip("/")
        if not rel.startswith(f"{day}/"):
            raise HTTPException(status_code=400, detail="이 공유 링크에서 열 수 없는 파일입니다.")
        try:
            file_path = Storage(user_dir).attachment_file(rel)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(file_path, filename=file_path.name)

    @app.post("/api/auth/register")
    async def auth_register(body: AuthBody) -> JSONResponse:
        state.reload()
        try:
            user = state.accounts.register(body.username, body.password)
        except AccountError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        ensure_user_root(state.config.data_root, user["id"])
        migrate_legacy_to_user(
            state.config.data_root,
            user["id"],
            chats=list(state.config.telegram.chats),
        )
        token = state.sessions.create(user["id"], name=user["username"])
        response = JSONResponse({"user": user, "telegram": {"linked": False, "api_ready": _api_ready(state.config)}})
        _set_cookie(response, COOKIE_USER, token)
        return response

    @app.post("/api/auth/login")
    async def auth_login(body: AuthBody) -> JSONResponse:
        state.reload()
        try:
            user = state.accounts.authenticate(body.username, body.password)
        except AccountError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        ensure_user_root(state.config.data_root, user["id"])
        token = state.sessions.create(user["id"], name=user["username"])
        account = state.accounts.get(user["id"]) or user
        payload = me_payload(user["id"], account)
        response = JSONResponse(payload)
        _set_cookie(response, COOKIE_USER, token)
        return response

    @app.post("/api/auth/logout")
    async def auth_logout(request: Request) -> JSONResponse:
        state.sessions.clear(request.cookies.get(COOKIE_USER))
        response = JSONResponse({"ok": True})
        _clear_cookie(response, COOKIE_USER)
        return response

    @app.get("/api/me")
    async def me(request: Request) -> dict:
        user_id, account = require_account(request)
        return me_payload(user_id, account)

    @app.get("/api/overview")
    async def overview(request: Request) -> dict:
        state.reload()
        session = current_session(request)
        base = {
            "timezone": state.config.timezone,
            "model": state.config.journal.ollama.model,
            "today": _today(state.config),
            "api_ready": _api_ready(state.config),
        }
        if not session:
            return {
                **base,
                "authenticated": False,
                "is_admin": False,
                "chat_count": 0,
                "journal_count": 0,
                "job": JobState().__dict__,
                "user": None,
                "telegram": {
                    "linked": False,
                    "authorized": False,
                    "user": None,
                    "message": "로그인이 필요합니다.",
                    "api_ready": _api_ready(state.config),
                },
                "login_stage": "idle",
            }

        user_id = str(session["user_id"])
        account = state.accounts.get(user_id)
        if not account:
            return {
                **base,
                "authenticated": False,
                "is_admin": False,
                "chat_count": 0,
                "journal_count": 0,
                "job": JobState().__dict__,
                "user": None,
                "telegram": {
                    "linked": False,
                    "authorized": False,
                    "user": None,
                    "message": "로그인이 필요합니다.",
                    "api_ready": _api_ready(state.config),
                },
                "login_stage": "idle",
            }

        cfg = user_config(state.config, user_id)
        linked = telegram_linked(cfg.data_root, cfg.telegram.session_name)
        login = state.login_for(user_id)
        meta = load_user_telegram(cfg.data_root)
        telegram: dict = {
            "linked": linked,
            "authorized": linked,
            "phone": meta.get("phone"),
            "api_ready": _api_ready(state.config),
            "user": None,
            "message": "텔레그램 연동이 필요합니다." if not linked else "연동됨",
        }
        if linked:
            if _api_ready(state.config):
                try:
                    status = await auth_status(cfg)
                    if status.get("authorized"):
                        telegram = {
                            **telegram,
                            **status,
                            "linked": True,
                            "phone": meta.get("phone"),
                            "api_ready": True,
                        }
                    else:
                        telegram["user"] = {
                            "id": meta.get("telegram_user_id"),
                            "name": meta.get("name") or session.get("name") or user_id,
                        }
                        telegram["message"] = "연동됨"
                except Exception as exc:
                    telegram["user"] = {
                        "id": meta.get("telegram_user_id"),
                        "name": meta.get("name") or session.get("name") or user_id,
                    }
                    telegram["message"] = str(exc)
            else:
                telegram["user"] = {
                    "id": meta.get("telegram_user_id"),
                    "name": meta.get("name") or session.get("name") or user_id,
                }

        storage = Storage(cfg.data_root)
        storage.ensure()
        return {
            **base,
            "authenticated": True,
            "is_admin": is_admin_username(str(account.get("username") or "")),
            "chat_count": len(cfg.telegram.chats) if linked else 0,
            "journal_count": len(storage.list_journal_dates()) if linked else 0,
            "job": state.runtime_for(user_id).snapshot_job() if linked else JobState().__dict__,
            "user": state.accounts.public_user(account),
            "telegram": telegram,
            "login_stage": "authorized" if linked else login.stage,
        }

    @app.get("/api/admin/users")
    async def admin_list_users(request: Request) -> dict:
        require_admin(request)
        state.reload()
        session_name = state.config.telegram.session_name
        last_seen = state.sessions.latest_seen_by_user()
        users = []
        for user in state.accounts.list_public():
            root = user_root(state.config.data_root, user["id"])
            journal_count = 0
            if root.is_dir():
                journal_count = len(Storage(root).list_journal_dates())
            users.append(
                {
                    **user,
                    "telegram_linked": telegram_linked(root, session_name),
                    "last_seen": last_seen.get(str(user["id"])),
                    "journal_count": journal_count,
                }
            )
        return {"users": users}

    @app.delete("/api/admin/users/{user_id}")
    async def admin_delete_user(request: Request, user_id: str) -> dict:
        admin_id, _ = require_admin(request)
        target = str(user_id)
        if target == str(admin_id):
            raise HTTPException(status_code=400, detail="자신의 계정은 삭제할 수 없습니다.")
        state.reload()
        account = state.accounts.get(target)
        if not account:
            raise HTTPException(status_code=404, detail="계정을 찾을 수 없습니다.")
        if not state.accounts.delete(target):
            raise HTTPException(status_code=404, detail="계정을 찾을 수 없습니다.")
        state.sessions.clear_user(target)
        with state.runtimes_lock:
            state.runtimes.pop(target, None)
        state.clear_login(target)
        delete_user_data(state.config.data_root, target)
        return {"ok": True, "id": target}

    @app.get("/api/telegram/status")
    async def telegram_status(request: Request) -> dict:
        user_id, account = require_account(request)
        return me_payload(user_id, account)

    @app.post("/api/telegram/login/start")
    async def login_start(request: Request, body: LinkStartBody) -> dict:
        user_id, _ = require_account(request)
        state.reload()
        if not _api_ready(state.config):
            raise HTTPException(
                status_code=503,
                detail="서버에 TELEGRAM_API_ID / TELEGRAM_API_HASH 를 설정하세요.",
            )
        phone = body.phone.strip()
        if not phone:
            raise HTTPException(status_code=400, detail="전화번호를 입력하세요.")
        save_user_telegram(ensure_user_root(state.config.data_root, user_id), {"phone": phone})
        cfg = user_config(state.config, user_id)
        login = state.login_for(user_id)
        try:
            result = await start_login(cfg, phone, login)
            return await finalize_telegram_link(user_id, result)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/telegram/login/code")
    async def login_code(request: Request, body: CodeBody) -> dict:
        user_id, _ = require_account(request)
        state.reload()
        if not _api_ready(state.config):
            raise HTTPException(
                status_code=503,
                detail="서버에 TELEGRAM_API_ID / TELEGRAM_API_HASH 를 설정하세요.",
            )
        cfg = user_config(state.config, user_id)
        login = state.login_for(user_id)
        try:
            result = await submit_code(cfg, body.code, login)
            return await finalize_telegram_link(user_id, result)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/telegram/login/password")
    async def login_password(request: Request, body: PasswordBody) -> dict:
        user_id, _ = require_account(request)
        state.reload()
        if not _api_ready(state.config):
            raise HTTPException(
                status_code=503,
                detail="서버에 TELEGRAM_API_ID / TELEGRAM_API_HASH 를 설정하세요.",
            )
        cfg = user_config(state.config, user_id)
        login = state.login_for(user_id)
        try:
            result = await submit_password(cfg, body.password, login)
            return await finalize_telegram_link(user_id, result)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/telegram/unlink")
    async def telegram_unlink(request: Request) -> dict:
        user_id, _ = require_account(request)
        state.reload()
        cfg = user_config(state.config, user_id)
        try:
            await logout(cfg, state.login_for(user_id))
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        state.clear_login(user_id)
        return {"linked": False, "message": "텔레그램 연동을 해제했습니다."}

    @app.get("/api/chats")
    async def list_chats(request: Request) -> dict:
        _, cfg = require_telegram(request)
        return {"chats": _watched_chats(cfg)}

    @app.post("/api/chats")
    async def add_chat(request: Request, body: ChatBody) -> dict:
        user_id, cfg = require_telegram(request)
        try:
            add_user_chat(cfg.data_root, body.id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        cfg = user_config(state.config, user_id)
        if body.title:
            Storage(cfg.data_root).save_chat_title(normalize_chat_ref(body.id), body.title)
        return {"chats": _watched_chats(cfg)}

    @app.post("/api/chats/delete")
    async def delete_chat(request: Request, body: ChatBody) -> dict:
        user_id, cfg = require_telegram(request)
        try:
            remove_user_chat(cfg.data_root, body.id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        cfg = user_config(state.config, user_id)
        return {"chats": _watched_chats(cfg)}

    @app.get("/api/dialogs")
    async def dialogs(request: Request) -> dict:
        from worklog_agent.collect import load_dialogs

        _, cfg = require_telegram(request)
        watched = {chat_ref_key(item) for item in cfg.telegram.chats}
        try:
            rows = await load_dialogs(cfg, interactive=False)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        storage = Storage(cfg.data_root)
        storage.save_chat_titles((row["id"], row["title"]) for row in rows)
        payload = []
        for row in rows:
            key = chat_ref_key(row["id"])
            payload.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "type": row["type"],
                    "watched": key in watched,
                }
            )
        return {"dialogs": payload}

    @app.get("/api/journals")
    async def journals(request: Request) -> dict:
        _, cfg = require_telegram(request)
        storage = Storage(cfg.data_root)
        dates = sorted(set(storage.list_journal_dates()) | set(storage.list_daily_dates()), reverse=True)
        items = []
        for day in dates:
            share = share_for_day(cfg.data_root, day)
            items.append(
                {
                    "date": day,
                    "has_journal": storage.journal_path(day).exists(),
                    "has_daily": storage.daily_path(day).exists(),
                    "attachments": len(storage.list_attachments(day)),
                    "share_token": share["token"] if share else None,
                }
            )
        return {"journals": items}

    @app.get("/api/journals/{day}")
    async def journal_detail(request: Request, day: str) -> dict:
        _, cfg = require_telegram(request)
        day = _require_day(day)
        payload = _journal_payload(Storage(cfg.data_root), day)
        share = share_for_day(cfg.data_root, day)
        payload["share_token"] = share["token"] if share else None
        payload["share_url"] = f"/s/{share['token']}" if share else None
        return payload

    @app.put("/api/journals/{day}")
    async def update_journal(request: Request, day: str, body: JournalBody) -> dict:
        _, cfg = require_telegram(request)
        day = _require_day(day)
        storage = Storage(cfg.data_root)
        path = storage.save_journal(day, body.markdown)
        return {
            "date": day,
            "markdown": storage.read_journal(day),
            "has_journal": True,
            "path": str(path),
            "attachments": storage.list_attachments(day),
        }

    @app.delete("/api/journals/{day}")
    async def delete_journal(request: Request, day: str) -> dict:
        _, cfg = require_telegram(request)
        day = _require_day(day)
        storage = Storage(cfg.data_root)
        if not storage.delete_journal(day):
            raise HTTPException(status_code=404, detail="삭제할 일지가 없습니다.")
        revoke_share_for_day(cfg.data_root, day)
        return {"date": day, "deleted": True}

    @app.post("/api/journals/{day}/share")
    async def share_journal(request: Request, day: str) -> dict:
        _, cfg = require_telegram(request)
        day = _require_day(day)
        storage = Storage(cfg.data_root)
        if not storage.journal_path(day).exists() and not storage.daily_path(day).exists():
            raise HTTPException(status_code=404, detail="공유할 일지가 없습니다.")
        meta = create_share_token(cfg.data_root, day)
        return {"date": day, "token": meta["token"], "url": f"/s/{meta['token']}"}

    @app.delete("/api/journals/{day}/share")
    async def unshare_journal(request: Request, day: str) -> dict:
        _, cfg = require_telegram(request)
        day = _require_day(day)
        removed = revoke_share_for_day(cfg.data_root, day)
        return {"date": day, "revoked": removed > 0}

    @app.get("/api/attachments/file")
    async def attachment_file(request: Request, path: str = Query(..., min_length=1)) -> FileResponse:
        _, cfg = require_telegram(request)
        try:
            file_path = Storage(cfg.data_root).attachment_file(path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(file_path, filename=file_path.name)

    @app.get("/api/job")
    async def job(request: Request) -> dict:
        user_id, _ = require_telegram(request)
        return state.runtime_for(user_id).snapshot_job()

    @app.post("/api/run")
    async def run_pipeline(request: Request, body: RunBody) -> dict:
        user_id, cfg = require_telegram(request)
        runtime = state.runtime_for(user_id)
        with runtime.job_lock:
            busy = runtime.job.status == "running" or (
                runtime.worker is not None and runtime.worker.is_alive()
            )
        if busy:
            raise HTTPException(status_code=409, detail="이미 실행 중입니다.")
        day = body.date
        snapshot = runtime.set_job(
            status="running",
            message="파이프라인을 시작합니다…",
            step="collect",
            date=day,
            path=None,
        )
        worker = threading.Thread(
            target=_run_pipeline_thread,
            args=(cfg, runtime, day),
            daemon=True,
            name=f"worklog-pipeline-{user_id}",
        )
        runtime.worker = worker
        worker.start()
        return snapshot

    return app


def _run_pipeline_thread(cfg: AppConfig, runtime: UserRuntime, day: str | None) -> None:
    try:
        asyncio.run(_run_pipeline(cfg, runtime, day))
    except Exception as exc:
        logger.exception("대시보드 파이프라인 스레드 실패")
        runtime.set_job(
            status="error",
            message=str(exc),
            step=runtime.snapshot_job().get("step"),
            date=day,
            path=None,
        )


async def _run_pipeline(cfg: AppConfig, runtime: UserRuntime, day: str | None) -> None:
    async def progress(message: str, step: str | None = None) -> None:
        current = runtime.snapshot_job()
        runtime.set_job(
            status="running",
            message=message,
            step=step or current.get("step"),
            date=day,
            path=None,
        )

    try:
        path = await Pipeline(cfg).run(day, on_progress=progress)
        runtime.set_job(
            status="done",
            message="일지 생성을 마쳤습니다.",
            step="done",
            date=day,
            path=path,
        )
    except Exception as exc:
        logger.exception("대시보드 파이프라인 실패")
        current = runtime.snapshot_job()
        runtime.set_job(
            status="error",
            message=str(exc),
            step=current.get("step"),
            date=day,
            path=None,
        )
