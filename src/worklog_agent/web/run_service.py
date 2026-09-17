from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from typing import Any

from worklog_agent.config import AppConfig
from worklog_agent.pipeline import Pipeline
from worklog_agent.storage import Storage
from worklog_agent.web.run_plan import dates_to_run, plan_run_days, resolve_run_dates

logger = logging.getLogger(__name__)


class RunBusyError(Exception):
    pass


class EmptyPlanError(Exception):
    pass


@dataclass
class JobState:
    status: str = "idle"
    message: str = ""
    step: str | None = None
    date: str | None = None
    path: str | None = None
    dates: list[str] = field(default_factory=list)
    total: int = 0
    done: int = 0
    results: list[dict] = field(default_factory=list)


@dataclass
class UserRuntime:
    job: JobState = field(default_factory=JobState)
    job_lock: threading.Lock = field(default_factory=threading.Lock)
    worker: threading.Thread | None = None

    def set_job(self, **kwargs: object) -> dict:
        with self.job_lock:
            current = dict(self.job.__dict__)
            current.update(kwargs)
            self.job = JobState(**current)  # type: ignore[arg-type]
            payload = dict(self.job.__dict__)
            payload["dates"] = list(payload.get("dates") or [])
            payload["results"] = list(payload.get("results") or [])
            return payload

    def snapshot_job(self) -> dict:
        with self.job_lock:
            payload = dict(self.job.__dict__)
            payload["dates"] = list(payload.get("dates") or [])
            payload["results"] = list(payload.get("results") or [])
            return payload


class RuntimeRegistry:
    def __init__(self) -> None:
        self._runtimes: dict[str, UserRuntime] = {}
        self._lock = threading.Lock()

    def runtime_for(self, user_id: int | str) -> UserRuntime:
        key = str(user_id)
        with self._lock:
            if key not in self._runtimes:
                self._runtimes[key] = UserRuntime()
            return self._runtimes[key]


def runtime_is_busy(runtime: UserRuntime) -> bool:
    with runtime.job_lock:
        return runtime.job.status == "running" or (
            runtime.worker is not None and runtime.worker.is_alive()
        )


def build_plan(cfg: AppConfig, body: dict[str, Any]) -> list[dict]:
    dates = resolve_run_dates(
        date=body.get("date"),
        start=body.get("start"),
        end=body.get("end"),
        dates=body.get("dates"),
    )
    storage = Storage(cfg.data_root)
    return plan_run_days(
        storage,
        dates,
        skip_existing=bool(body.get("skip_existing")),
        regenerate_if_stale=bool(body.get("regenerate_if_stale")),
        force=bool(body.get("force")),
        tz_name=cfg.timezone,
    )


def enqueue_planned_run(
    _base_config: AppConfig,
    user_id: int | str,
    cfg: AppConfig,
    runtime: UserRuntime,
    body: dict[str, Any],
    *,
    wait: bool = False,
) -> dict:
    if runtime_is_busy(runtime):
        raise RunBusyError("이미 실행 중입니다.")
    from worklog_agent.journal import normalize_generate_type

    generate_type = normalize_generate_type(body.get("generate_type"))
    plan = build_plan(cfg, body)
    queue = dates_to_run(plan)
    if not queue:
        raise EmptyPlanError("생성할 날짜가 없습니다.")
    snapshot = runtime.set_job(
        status="running",
        message="파이프라인을 시작합니다…",
        step="collect",
        date=queue[0],
        path=None,
        dates=[item["date"] for item in plan],
        total=len(plan),
        done=0,
        results=[],
    )
    worker = threading.Thread(
        target=_run_pipeline_thread,
        args=(cfg, runtime, plan, generate_type),
        daemon=True,
        name=f"worklog-pipeline-{user_id}",
    )
    runtime.worker = worker
    worker.start()
    if wait:
        worker.join()
    return snapshot


def _run_pipeline_thread(
    cfg: AppConfig,
    runtime: UserRuntime,
    plan: list[dict],
    generate_type: str = "combined",
) -> None:
    try:
        asyncio.run(_run_pipeline_queue(cfg, runtime, plan, generate_type))
    except Exception as exc:
        logger.exception("대시보드 파이프라인 스레드 실패")
        runtime.set_job(
            status="error",
            message=str(exc),
            step=runtime.snapshot_job().get("step"),
        )


async def _run_pipeline_queue(
    cfg: AppConfig,
    runtime: UserRuntime,
    plan: list[dict],
    generate_type: str = "combined",
) -> None:
    results: list[dict] = []
    total = len(plan)
    plan_dates = [item["date"] for item in plan]
    last_path: str | None = None
    had_error = False

    def processed_count() -> int:
        return len(results)

    for item in plan:
        day = item["date"]
        if item["action"] == "skip":
            results.append({"date": day, "status": "skipped", "reason": item.get("reason")})
            runtime.set_job(
                status="running",
                message=f"{day} · 건너뜀 (일지 있음)",
                step="done",
                date=day,
                path=last_path,
                dates=plan_dates,
                total=total,
                done=processed_count(),
                results=list(results),
            )
            continue

        async def progress(message: str, step: str | None = None, *, current_day: str = day) -> None:
            current = runtime.snapshot_job()
            runtime.set_job(
                status="running",
                message=message,
                step=step or current.get("step"),
                date=current_day,
                path=last_path,
                dates=plan_dates,
                total=total,
                done=processed_count(),
                results=list(results),
            )

        try:
            path = await Pipeline(cfg).run(
                day,
                generate_type=generate_type,
                on_progress=progress,
            )
            last_path = path
            results.append({"date": day, "status": "done", "path": path})
        except Exception as exc:
            logger.exception("대시보드 파이프라인 실패: %s", day)
            had_error = True
            results.append({"date": day, "status": "error", "message": str(exc)})

        runtime.set_job(
            status="running",
            message=f"{day} · 완료",
            step="done",
            date=day,
            path=last_path,
            dates=plan_dates,
            total=total,
            done=processed_count(),
            results=list(results),
        )

    done_count = sum(1 for row in results if row["status"] == "done")
    skip_count = sum(1 for row in results if row["status"] == "skipped")
    error_count = sum(1 for row in results if row["status"] == "error")
    if had_error:
        runtime.set_job(
            status="error",
            message=f"완료 {done_count} · 건너뜀 {skip_count} · 실패 {error_count}",
            step="done",
            date=results[-1]["date"] if results else None,
            path=last_path,
            dates=plan_dates,
            total=total,
            done=total,
            results=results,
        )
        return
    runtime.set_job(
        status="done",
        message=f"일지 생성을 마쳤습니다. (완료 {done_count} · 건너뜀 {skip_count})",
        step="done",
        date=results[-1]["date"] if results else None,
        path=last_path,
        dates=plan_dates,
        total=total,
        done=total,
        results=results,
    )
