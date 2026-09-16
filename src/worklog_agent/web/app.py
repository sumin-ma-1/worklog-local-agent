from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from worklog_agent.config import (
    AppConfig,
    chat_ref_key,
    load_config,
    normalize_chat_ref,
    save_telegram_credentials,
    telegram_credential_summary,
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
    find_share,
    migrate_legacy_to_user,
    move_pending_to_user,
    pending_config,
    remove_user_chat,
    revoke_share_for_day,
    share_for_day,
    user_config,
)
from worklog_agent.web.auth_session import COOKIE_LOGIN, COOKIE_USER, SessionStore

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

    def login_for(self, login_id: str) -> LoginSession:
        with self.logins_lock:
            if login_id not in self.logins:
                self.logins[login_id] = LoginSession()
            return self.logins[login_id]

    def clear_login(self, login_id: str | None) -> None:
        if not login_id:
            return
        with self.logins_lock:
            self.logins.pop(login_id, None)


class ChatBody(BaseModel):
    id: str | int
    title: str | None = None


class JournalBody(BaseModel):
    markdown: str


class RunBody(BaseModel):
    date: str | None = Field(default=None)


class LoginStartBody(BaseModel):
    phone: str
    api_id: str | int | None = None
    api_hash: str | None = None


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
    )

    app = FastAPI(title="worklog-local-agent", docs_url=None, redoc_url=None)
    app.state.dashboard = state
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))

    def current_user(request: Request) -> dict | None:
        return state.sessions.resolve(request.cookies.get(COOKIE_USER))

    def require_user(request: Request) -> tuple[str, AppConfig]:
        session = current_user(request)
        if not session:
            raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
        user_id = str(session["user_id"])
        state.reload()
        cfg = user_config(state.config, user_id)
        session_base = Storage(cfg.data_root).session_path(cfg.telegram.session_name)
        if not Path(f"{session_base}.session").is_file():
            raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
        return user_id, cfg

    def ensure_login_id(request: Request) -> str:
        return request.cookies.get(COOKIE_LOGIN) or secrets.token_urlsafe(24)

    def attach_login_cookie(response: JSONResponse, request: Request, login_id: str) -> None:
        if not request.cookies.get(COOKIE_LOGIN):
            _set_cookie(response, COOKIE_LOGIN, login_id)

    async def finalize_auth_result(
        request: Request,
        *,
        login_id: str,
        result: dict,
    ) -> JSONResponse:
        response = JSONResponse(result)
        user = result.get("user") if isinstance(result.get("user"), dict) else None
        if result.get("stage") == "authorized" and user and user.get("id") is not None:
            user_id = int(user["id"])
            state.reload()
            migrate_legacy_to_user(
                state.config.data_root,
                user_id,
                chats=list(state.config.telegram.chats),
            )
            move_pending_to_user(state.config.data_root, login_id, user_id)
            token = state.sessions.create(user_id, name=str(user.get("name") or user_id))
            _set_cookie(response, COOKIE_USER, token)
            _clear_cookie(response, COOKIE_LOGIN)
            state.clear_login(login_id)
            result = {**result, "authorized": True}
            response = JSONResponse(result)
            _set_cookie(response, COOKIE_USER, token)
            _clear_cookie(response, COOKIE_LOGIN)
        else:
            attach_login_cookie(response, request, login_id)
        return response

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
        storage = Storage(user_dir)
        return _journal_payload(storage, day)

    @app.get("/api/share/{token}/file")
    async def share_file(token: str, path: str = Query(..., min_length=1)) -> FileResponse:
        state.reload()
        found = find_share(state.config.data_root, token)
        if not found:
            raise HTTPException(status_code=404, detail="공유 링크가 없거나 만료되었습니다.")
        user_dir, meta = found
        day = str(meta.get("day") or "")
        # Restrict to attachments under the shared day.
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

    @app.get("/api/overview")
    async def overview(request: Request) -> dict:
        state.reload()
        credentials = telegram_credential_summary(state.config)
        session = current_user(request)
        login_id = request.cookies.get(COOKIE_LOGIN)
        login_stage = state.login_for(login_id).stage if login_id else "idle"

        if not session:
            return {
                "timezone": state.config.timezone,
                "model": state.config.journal.ollama.model,
                "today": _today(state.config),
                "chat_count": 0,
                "journal_count": 0,
                "job": JobState().__dict__,
                "credentials": credentials,
                "telegram": {
                    "authorized": False,
                    "user": None,
                    "message": "텔레그램 로그인이 필요합니다.",
                },
                "login_stage": login_stage if login_stage != "authorized" else "idle",
            }

        user_id = str(session["user_id"])
        cfg = user_config(state.config, user_id)
        storage = Storage(cfg.data_root)
        storage.ensure()
        session_base = storage.session_path(cfg.telegram.session_name)
        has_session_file = Path(f"{session_base}.session").is_file()
        telegram = {
            "authorized": False,
            "user": None,
            "message": "텔레그램 로그인이 필요합니다.",
        }
        if credentials["ready"] and has_session_file:
            try:
                telegram = await auth_status(cfg)
            except Exception as exc:
                telegram = {"authorized": False, "user": None, "message": str(exc)}
        if has_session_file and not telegram.get("authorized"):
            telegram = {
                "authorized": True,
                "user": {
                    "id": int(user_id) if user_id.isdigit() else user_id,
                    "name": session.get("name") or user_id,
                    "username": None,
                    "phone": None,
                },
                "message": "로그인됨",
            }
        if not has_session_file:
            return {
                "timezone": state.config.timezone,
                "model": state.config.journal.ollama.model,
                "today": _today(state.config),
                "chat_count": 0,
                "journal_count": 0,
                "job": JobState().__dict__,
                "credentials": credentials,
                "telegram": {
                    "authorized": False,
                    "user": None,
                    "message": "텔레그램 로그인이 필요합니다.",
                },
                "login_stage": "idle",
            }

        return {
            "timezone": cfg.timezone,
            "model": cfg.journal.ollama.model,
            "today": _today(cfg),
            "chat_count": len(cfg.telegram.chats),
            "journal_count": len(storage.list_journal_dates()),
            "job": state.runtime_for(user_id).snapshot_job(),
            "credentials": credentials,
            "telegram": telegram,
            "login_stage": "authorized",
        }

    @app.get("/api/telegram/status")
    async def telegram_status(request: Request) -> dict:
        state.reload()
        credentials = telegram_credential_summary(state.config)
        session = current_user(request)
        login_id = request.cookies.get(COOKIE_LOGIN)
        login_stage = state.login_for(login_id).stage if login_id else "idle"
        if not session:
            return {
                "credentials": credentials,
                "telegram": {
                    "authorized": False,
                    "user": None,
                    "message": "텔레그램 로그인이 필요합니다.",
                },
                "login_stage": login_stage,
            }
        cfg = user_config(state.config, session["user_id"])
        try:
            telegram = await auth_status(cfg) if credentials["ready"] else {
                "authorized": False,
                "user": None,
                "message": "API ID / Hash 가 필요합니다.",
            }
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {
            "credentials": credentials,
            "telegram": telegram,
            "login_stage": "authorized" if telegram.get("authorized") else login_stage,
        }

    @app.post("/api/telegram/login/start")
    async def login_start(request: Request, body: LoginStartBody) -> JSONResponse:
        state.reload()
        login_id = ensure_login_id(request)
        login = state.login_for(login_id)
        try:
            api_id = body.api_id if body.api_id not in (None, "") else state.config.env.telegram_api_id
            api_hash = body.api_hash if body.api_hash not in (None, "") else state.config.env.telegram_api_hash
            if not api_id or not api_hash:
                raise ValueError("API ID / Hash 를 입력하세요.")
            state.config = save_telegram_credentials(
                state.config,
                api_id=api_id,
                api_hash=api_hash,
                phone=body.phone,
            )
            cfg = pending_config(state.config, login_id)
            result = await start_login(cfg, body.phone, login)
            return await finalize_auth_result(request, login_id=login_id, result=result)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/telegram/login/code")
    async def login_code(request: Request, body: CodeBody) -> JSONResponse:
        state.reload()
        login_id = ensure_login_id(request)
        login = state.login_for(login_id)
        try:
            cfg = pending_config(state.config, login_id)
            result = await submit_code(cfg, body.code, login)
            return await finalize_auth_result(request, login_id=login_id, result=result)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/telegram/login/password")
    async def login_password(request: Request, body: PasswordBody) -> JSONResponse:
        state.reload()
        login_id = ensure_login_id(request)
        login = state.login_for(login_id)
        try:
            cfg = pending_config(state.config, login_id)
            result = await submit_password(cfg, body.password, login)
            return await finalize_auth_result(request, login_id=login_id, result=result)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/telegram/logout")
    async def telegram_logout(request: Request) -> JSONResponse:
        state.reload()
        session = current_user(request)
        login_id = request.cookies.get(COOKIE_LOGIN)
        try:
            if session:
                user_id = str(session["user_id"])
                cfg = user_config(state.config, user_id)
                login = LoginSession(stage="authorized")
                result = await logout(cfg, login)
                state.sessions.clear(request.cookies.get(COOKIE_USER))
                state.clear_login(login_id)
            elif login_id:
                cfg = pending_config(state.config, login_id)
                result = await logout(cfg, state.login_for(login_id))
                state.clear_login(login_id)
            else:
                result = {"stage": "idle", "message": "로그아웃되었습니다.", "authorized": False}
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        state.reload()
        response = JSONResponse(
            {
                **result,
                "credentials": telegram_credential_summary(state.config),
            }
        )
        _clear_cookie(response, COOKIE_USER)
        _clear_cookie(response, COOKIE_LOGIN)
        return response

    @app.get("/api/chats")
    async def list_chats(request: Request) -> dict:
        _, cfg = require_user(request)
        return {"chats": _watched_chats(cfg)}

    @app.post("/api/chats")
    async def add_chat(request: Request, body: ChatBody) -> dict:
        user_id, cfg = require_user(request)
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
        user_id, cfg = require_user(request)
        try:
            remove_user_chat(cfg.data_root, body.id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        cfg = user_config(state.config, user_id)
        return {"chats": _watched_chats(cfg)}

    @app.get("/api/dialogs")
    async def dialogs(request: Request) -> dict:
        from worklog_agent.collect import load_dialogs

        _, cfg = require_user(request)
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
        _, cfg = require_user(request)
        storage = Storage(cfg.data_root)
        dates = sorted(set(storage.list_journal_dates()) | set(storage.list_daily_dates()), reverse=True)
        items = []
        for day in dates:
            journal_path = storage.journal_path(day)
            daily_path = storage.daily_path(day)
            share = share_for_day(cfg.data_root, day)
            items.append(
                {
                    "date": day,
                    "has_journal": journal_path.exists(),
                    "has_daily": daily_path.exists(),
                    "attachments": len(storage.list_attachments(day)),
                    "share_token": share["token"] if share else None,
                }
            )
        return {"journals": items}

    @app.get("/api/journals/{day}")
    async def journal_detail(request: Request, day: str) -> dict:
        _, cfg = require_user(request)
        day = _require_day(day)
        storage = Storage(cfg.data_root)
        payload = _journal_payload(storage, day)
        share = share_for_day(cfg.data_root, day)
        payload["share_token"] = share["token"] if share else None
        payload["share_url"] = f"/s/{share['token']}" if share else None
        return payload

    @app.put("/api/journals/{day}")
    async def update_journal(request: Request, day: str, body: JournalBody) -> dict:
        _, cfg = require_user(request)
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
        _, cfg = require_user(request)
        day = _require_day(day)
        storage = Storage(cfg.data_root)
        if not storage.delete_journal(day):
            raise HTTPException(status_code=404, detail="삭제할 일지가 없습니다.")
        revoke_share_for_day(cfg.data_root, day)
        return {"date": day, "deleted": True}

    @app.post("/api/journals/{day}/share")
    async def share_journal(request: Request, day: str) -> dict:
        _, cfg = require_user(request)
        day = _require_day(day)
        storage = Storage(cfg.data_root)
        if not storage.journal_path(day).exists() and not storage.daily_path(day).exists():
            raise HTTPException(status_code=404, detail="공유할 일지가 없습니다.")
        meta = create_share_token(cfg.data_root, day)
        return {
            "date": day,
            "token": meta["token"],
            "url": f"/s/{meta['token']}",
        }

    @app.delete("/api/journals/{day}/share")
    async def unshare_journal(request: Request, day: str) -> dict:
        _, cfg = require_user(request)
        day = _require_day(day)
        removed = revoke_share_for_day(cfg.data_root, day)
        return {"date": day, "revoked": removed > 0}

    @app.get("/api/attachments/file")
    async def attachment_file(request: Request, path: str = Query(..., min_length=1)) -> FileResponse:
        _, cfg = require_user(request)
        try:
            file_path = Storage(cfg.data_root).attachment_file(path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(file_path, filename=file_path.name)

    @app.get("/api/job")
    async def job(request: Request) -> dict:
        user_id, _ = require_user(request)
        return state.runtime_for(user_id).snapshot_job()

    @app.post("/api/run")
    async def run_pipeline(request: Request, body: RunBody) -> dict:
        user_id, cfg = require_user(request)
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
