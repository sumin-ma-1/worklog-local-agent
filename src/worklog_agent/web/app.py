from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from worklog_agent.config import (
    AppConfig,
    add_chat_ref,
    chat_ref_key,
    load_config,
    normalize_chat_ref,
    remove_chat_ref,
)
from worklog_agent.pipeline import Pipeline
from worklog_agent.storage import Storage

logger = logging.getLogger(__name__)
WEB_DIR = Path(__file__).parent


@dataclass
class JobState:
    status: str = "idle"
    message: str = ""
    date: str | None = None
    path: str | None = None


@dataclass
class DashboardState:
    config_path: Path
    config: AppConfig
    job: JobState = field(default_factory=JobState)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    task: asyncio.Task | None = None

    def reload(self) -> AppConfig:
        self.config = load_config(self.config_path)
        return self.config

    def storage(self) -> Storage:
        storage = Storage(self.config.data_root)
        storage.ensure()
        return storage


class ChatBody(BaseModel):
    id: str | int


class RunBody(BaseModel):
    date: str | None = Field(default=None)


def _today(config: AppConfig) -> str:
    return datetime.now(ZoneInfo(config.timezone)).date().isoformat()


def _watched_chats(state: DashboardState) -> list[dict[str, object]]:
    storage = state.storage()
    rows: list[dict[str, object]] = []
    for spec in state.config.telegram.chats:
        ref = normalize_chat_ref(spec)
        row: dict[str, object] = {
            "id": ref,
            "key": chat_ref_key(ref),
            "title": str(ref),
            "last_id": 0,
            "last_collected_at": None,
        }
        if isinstance(ref, int):
            meta = storage.watched_chat_meta(ref)
            if meta.get("title"):
                row["title"] = meta["title"]
            row["last_id"] = meta.get("last_id") or 0
            row["last_collected_at"] = meta.get("last_collected_at")
        rows.append(row)
    return rows


def create_app(config_path: Path | None = None) -> FastAPI:
    path = Path(config_path or "config.yaml").expanduser()
    if not path.is_absolute():
        path = path.resolve()
    state = DashboardState(config_path=path, config=load_config(path))

    app = FastAPI(title="worklog-local-agent", docs_url=None, redoc_url=None)
    app.state.dashboard = state
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "index.html",
            {"today": _today(state.config)},
        )

    @app.get("/api/overview")
    async def overview() -> dict:
        state.reload()
        storage = state.storage()
        return {
            "timezone": state.config.timezone,
            "model": state.config.journal.ollama.model,
            "today": _today(state.config),
            "chat_count": len(state.config.telegram.chats),
            "journal_count": len(storage.list_journal_dates()),
            "job": state.job.__dict__,
        }

    @app.get("/api/chats")
    async def list_chats() -> dict:
        state.reload()
        return {"chats": _watched_chats(state)}

    @app.post("/api/chats")
    async def add_chat(body: ChatBody) -> dict:
        state.reload()
        try:
            state.config = add_chat_ref(state.config, body.id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"chats": _watched_chats(state)}

    @app.post("/api/chats/delete")
    async def delete_chat(body: ChatBody) -> dict:
        state.reload()
        try:
            state.config = remove_chat_ref(state.config, body.id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"chats": _watched_chats(state)}

    @app.get("/api/dialogs")
    async def dialogs() -> dict:
        from worklog_agent.collect import load_dialogs

        state.reload()
        watched = {chat_ref_key(item) for item in state.config.telegram.chats}
        try:
            rows = await load_dialogs(state.config, interactive=False)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
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
    async def journals() -> dict:
        storage = state.storage()
        dates = sorted(set(storage.list_journal_dates()) | set(storage.list_daily_dates()), reverse=True)
        items = []
        for day in dates:
            journal_path = storage.journal_path(day)
            daily_path = storage.daily_path(day)
            items.append(
                {
                    "date": day,
                    "has_journal": journal_path.exists(),
                    "has_daily": daily_path.exists(),
                    "attachments": len(storage.list_attachments(day)),
                }
            )
        return {"journals": items}

    @app.get("/api/journals/{day}")
    async def journal_detail(day: str) -> dict:
        storage = state.storage()
        markdown = ""
        daily = None
        if storage.journal_path(day).exists():
            markdown = storage.read_journal(day)
        if storage.daily_path(day).exists():
            daily = storage.load_daily(day).model_dump(mode="json")
        return {
            "date": day,
            "markdown": markdown,
            "daily": daily,
            "attachments": storage.list_attachments(day),
        }

    @app.get("/api/attachments/file")
    async def attachment_file(path: str = Query(..., min_length=1)) -> FileResponse:
        try:
            file_path = state.storage().attachment_file(path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(file_path, filename=file_path.name)

    @app.get("/api/job")
    async def job() -> dict:
        return state.job.__dict__

    @app.post("/api/run")
    async def run_pipeline(body: RunBody) -> dict:
        if state.job.status == "running":
            raise HTTPException(status_code=409, detail="이미 실행 중입니다.")
        state.reload()
        state.job = JobState(status="running", message="파이프라인 실행 중", date=body.date)
        state.task = asyncio.create_task(_run_pipeline(state, body.date))
        return state.job.__dict__

    return app


async def _run_pipeline(state: DashboardState, day: str | None) -> None:
    async with state.lock:
        try:
            path = await Pipeline(state.config).run(day)
            state.job = JobState(
                status="done",
                message="일지 생성을 마쳤습니다.",
                date=day,
                path=path,
            )
        except Exception as exc:
            logger.exception("대시보드 파이프라인 실패")
            state.job = JobState(status="error", message=str(exc), date=day)
