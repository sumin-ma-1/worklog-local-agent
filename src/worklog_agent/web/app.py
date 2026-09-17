from __future__ import annotations

import json
import logging
import re
import threading
from contextlib import asynccontextmanager
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
from worklog_agent.journal_prompt import (
    PromptError,
    load_system_prompt,
    reset_system_prompt,
    save_system_prompt,
)
from worklog_agent.schedules import (
    ScheduleError,
    create_schedule,
    delete_schedule,
    load_schedules,
    update_schedule,
)
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
    load_user_chats,
    load_user_preferences,
    load_user_telegram,
    migrate_legacy_to_user,
    remove_user_chat,
    revoke_share_for_day,
    save_user_preferences,
    save_user_telegram,
    share_for_day,
    telegram_linked,
    user_config,
    user_root,
)
from worklog_agent.web.auth_session import COOKIE_USER, SessionStore
from worklog_agent.journal_meta import delete_journal_meta
from worklog_agent.web.file_icons import file_icon_src
from worklog_agent.web.run_plan import dates_to_run, plan_run_days, resolve_run_dates
from worklog_agent.web.run_service import (
    EmptyPlanError,
    JobState,
    RunBusyError,
    UserRuntime,
    enqueue_planned_run,
)

logger = logging.getLogger(__name__)
WEB_DIR = Path(__file__).parent
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_COOKIE_MAX_AGE = 60 * 60 * 24 * 180


def _require_day(day: str) -> str:
    if not _DAY_RE.fullmatch(day):
        raise HTTPException(status_code=400, detail="날짜 형식이 올바르지 않습니다. YYYY-MM-DD")
    return day


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
    start: str | None = Field(default=None)
    end: str | None = Field(default=None)
    dates: list[str] | None = Field(default=None)
    skip_existing: bool = False
    regenerate_if_stale: bool = False
    force: bool = False


class ScheduleBody(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    enabled: bool = True
    time: str = Field(min_length=5, max_length=5)
    target: str = Field(default="yesterday")
    skip_existing: bool = True
    regenerate_if_stale: bool = False
    force: bool = False


class SchedulePatchBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    enabled: bool | None = None
    time: str | None = Field(default=None, min_length=5, max_length=5)
    target: str | None = None
    skip_existing: bool | None = None
    regenerate_if_stale: bool | None = None
    force: bool | None = None


class JournalPromptBody(BaseModel):
    sections: list[str] = Field(min_length=1, max_length=20)


class JournalAskBody(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class PreferencesBody(BaseModel):
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    model: str | None = Field(default=None, min_length=1, max_length=128)


class AuthBody(BaseModel):
    username: str
    password: str


class LinkStartBody(BaseModel):
    phone: str


class CodeBody(BaseModel):
    code: str


class PasswordBody(BaseModel):
    password: str


def _validate_timezone(name: str) -> str:
    value = str(name or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="시간대를 입력하세요.")
    try:
        ZoneInfo(value)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 시간대입니다: {value}") from exc
    return value


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

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop = threading.Event()

        def scheduler_loop() -> None:
            while not stop.wait(60):
                try:
                    from worklog_agent.schedules import tick_all_schedules

                    tick_all_schedules(state.config, state.runtime_for)
                except Exception:
                    logger.exception("예약 실행 tick 실패")

        thread = threading.Thread(target=scheduler_loop, daemon=True, name="worklog-scheduler")
        thread.start()
        yield
        stop.set()

    app = FastAPI(title="worklog-local-agent", docs_url=None, redoc_url=None, lifespan=lifespan)
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
                "icon": file_icon_src(str(file.get("name") or "")),
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
            "timezone": cfg.timezone,
            "model": cfg.journal.ollama.model,
            "default_model": state.config.journal.ollama.model,
            "today": _today(cfg),
            "api_ready": _api_ready(state.config),
            "authenticated": True,
            "is_admin": is_admin_username(str(account.get("username") or "")),
            "chat_count": len(cfg.telegram.chats) if linked else 0,
            "journal_count": len(storage.list_journal_dates()) if linked else 0,
            "job": state.runtime_for(user_id).snapshot_job() if linked else JobState().__dict__,
            "user": state.accounts.public_user(account),
            "telegram": telegram,
            "login_stage": "authorized" if linked else login.stage,
        }

    @app.get("/api/models")
    async def list_models(request: Request) -> dict:
        user_id, _ = require_account(request)
        cfg = user_config(state.config, user_id)
        from worklog_agent.ollama import OllamaError, list_model_names

        current = cfg.journal.ollama.model
        try:
            models = await list_model_names(cfg)
        except OllamaError:
            models = [current] if current else []
        if current and current not in models:
            models = [current, *models]
        return {"models": models, "current": current, "default": state.config.journal.ollama.model}

    @app.get("/api/preferences")
    async def get_preferences(request: Request) -> dict:
        user_id, _ = require_account(request)
        cfg = user_config(state.config, user_id)
        root = ensure_user_root(state.config.data_root, user_id)
        prefs = load_user_preferences(root)
        return {
            "timezone": cfg.timezone,
            "model": cfg.journal.ollama.model,
            "default_model": state.config.journal.ollama.model,
            "today": _today(cfg),
            "saved": {
                "timezone": prefs.get("timezone"),
                "model": prefs.get("model"),
            },
        }

    @app.patch("/api/preferences")
    async def patch_preferences(request: Request, body: PreferencesBody) -> dict:
        user_id, _ = require_account(request)
        root = ensure_user_root(state.config.data_root, user_id)
        updates: dict = {}
        if body.timezone is not None:
            updates["timezone"] = _validate_timezone(body.timezone)
        if body.model is not None:
            model = str(body.model).strip()
            if not model:
                raise HTTPException(status_code=400, detail="모델을 입력하세요.")
            updates["model"] = model
        if updates:
            save_user_preferences(root, updates)
        cfg = user_config(state.config, user_id)
        return {
            "timezone": cfg.timezone,
            "model": cfg.journal.ollama.model,
            "today": _today(cfg),
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
            chat_count = 0
            if root.is_dir():
                journal_count = len(Storage(root).list_journal_dates())
                chat_count = len(load_user_chats(root))
            users.append(
                {
                    **user,
                    "telegram_linked": telegram_linked(root, session_name),
                    "last_seen": last_seen.get(str(user["id"])),
                    "journal_count": journal_count,
                    "chat_count": chat_count,
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
        delete_journal_meta(storage, day)
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

    @app.post("/api/journals/ask")
    async def ask_journals(request: Request, body: JournalAskBody) -> dict:
        _, cfg = require_telegram(request)
        storage = Storage(cfg.data_root)
        dates = sorted(set(storage.list_journal_dates()) | set(storage.list_daily_dates()), reverse=True)
        snippets: list[str] = []
        for day in dates[:40]:
            text = (storage.read_journal(day) or "").strip()
            if not text:
                continue
            snippets.append(f"## {day}\n{text[:1200]}")
        if not snippets:
            return {
                "answer": "아직 검색할 일지가 없습니다. 먼저 일지를 생성해 주세요.",
                "days": [],
            }
        corpus = "\n\n".join(snippets)
        system = (
            "당신은 사용자의 업무 일지 검색 비서입니다. "
            "아래 일지 내용만 근거로 한국어로 짧고 정확하게 답하세요. "
            "관련 날짜가 있으면 답변 끝에 한 줄로 DAY:YYYY-MM-DD 형식으로 적어 주세요. "
            "여러 날이면 가장 관련 있는 하루만 적으세요. 근거가 없으면 모른다고 말하세요."
        )
        user_prompt = f"질문: {body.question.strip()}\n\n일지:\n{corpus}"
        try:
            from worklog_agent.ollama import OllamaError, chat as ollama_chat

            answer = await ollama_chat(cfg, system, user_prompt, model=cfg.journal.ollama.model)
        except OllamaError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"일지 질의 실패: {exc}") from exc

        days: list[str] = []
        match = re.search(r"DAY:(\d{4}-\d{2}-\d{2})", answer or "")
        if match:
            days.append(match.group(1))
            answer = re.sub(r"\n?DAY:\d{4}-\d{2}-\d{2}\s*$", "", answer).strip()
        return {"answer": answer or "답변을 만들지 못했습니다.", "days": days}

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

    @app.post("/api/run/plan")
    async def run_plan(request: Request, body: RunBody) -> dict:
        _, cfg = require_telegram(request)
        try:
            dates = resolve_run_dates(
                date=body.date,
                start=body.start,
                end=body.end,
                dates=body.dates,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        for day in dates:
            _require_day(day)
        storage = Storage(cfg.data_root)
        plan = plan_run_days(
            storage,
            dates,
            skip_existing=body.skip_existing,
            regenerate_if_stale=body.regenerate_if_stale,
            force=body.force,
            tz_name=cfg.timezone,
        )
        return {
            "dates": dates,
            "plan": plan,
            "run_count": len(dates_to_run(plan)),
            "skip_count": sum(1 for item in plan if item["action"] == "skip"),
        }

    @app.get("/api/job")
    async def job(request: Request) -> dict:
        user_id, _ = require_telegram(request)
        return state.runtime_for(user_id).snapshot_job()

    @app.post("/api/run")
    async def run_pipeline(request: Request, body: RunBody) -> dict:
        user_id, cfg = require_telegram(request)
        runtime = state.runtime_for(user_id)
        payload = body.model_dump()
        try:
            dates = resolve_run_dates(
                date=body.date,
                start=body.start,
                end=body.end,
                dates=body.dates,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        for day in dates:
            _require_day(day)
        try:
            return enqueue_planned_run(state.config, user_id, cfg, runtime, payload)
        except RunBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except EmptyPlanError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/schedules")
    async def list_schedules(request: Request) -> dict:
        user_id, cfg = require_telegram(request)
        root = user_root(cfg.data_root, user_id)
        return {"schedules": load_schedules(root)}

    @app.post("/api/schedules")
    async def add_schedule(request: Request, body: ScheduleBody) -> dict:
        user_id, cfg = require_telegram(request)
        root = user_root(cfg.data_root, user_id)
        try:
            item = create_schedule(root, body.model_dump())
        except ScheduleError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"schedule": item}

    @app.patch("/api/schedules/{schedule_id}")
    async def patch_schedule(request: Request, schedule_id: str, body: SchedulePatchBody) -> dict:
        user_id, cfg = require_telegram(request)
        root = user_root(cfg.data_root, user_id)
        payload = body.model_dump(exclude_unset=True)
        if not payload:
            raise HTTPException(status_code=400, detail="변경할 항목이 없습니다.")
        try:
            item = update_schedule(root, schedule_id, payload)
        except ScheduleError as exc:
            raise HTTPException(status_code=404 if "찾을" in str(exc) else 400, detail=str(exc)) from exc
        return {"schedule": item}

    @app.delete("/api/schedules/{schedule_id}")
    async def remove_schedule(request: Request, schedule_id: str) -> dict:
        user_id, cfg = require_telegram(request)
        root = user_root(cfg.data_root, user_id)
        if not delete_schedule(root, schedule_id):
            raise HTTPException(status_code=404, detail="예약을 찾을 수 없습니다.")
        return {"deleted": True, "id": schedule_id}

    @app.get("/api/journal-prompt")
    async def get_journal_prompt(request: Request) -> dict:
        _, cfg = require_telegram(request)
        return load_system_prompt(cfg.data_root)

    @app.put("/api/journal-prompt")
    async def put_journal_prompt(request: Request, body: JournalPromptBody) -> dict:
        _, cfg = require_telegram(request)
        try:
            return save_system_prompt(cfg.data_root, body.sections)
        except PromptError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/journal-prompt/reset")
    async def reset_journal_prompt(request: Request) -> dict:
        _, cfg = require_telegram(request)
        return reset_system_prompt(cfg.data_root)

    return app
