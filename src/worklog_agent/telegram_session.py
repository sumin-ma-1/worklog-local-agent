"""Single-thread Telegram session access so the uvicorn loop never blocks."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Awaitable, Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TypeVar

T = TypeVar("T")

logger = logging.getLogger(__name__)

_JOBS: list[tuple[Callable[[], Awaitable[object]], Future, float | None]] = []
_JOBS_LOCK = threading.Lock()
_WAKE = threading.Event()
_WORKER_STARTED = False
_WORKER_LOCK = threading.Lock()
_WAIT_POOL = ThreadPoolExecutor(max_workers=16, thread_name_prefix="tg-wait")


def _ensure_worker() -> None:
    global _WORKER_STARTED
    with _WORKER_LOCK:
        if _WORKER_STARTED:
            return
        thread = threading.Thread(target=_worker_loop, name="worklog-telegram", daemon=True)
        thread.start()
        _WORKER_STARTED = True


def _worker_loop() -> None:
    while True:
        _WAKE.wait(timeout=1.0)
        while True:
            with _JOBS_LOCK:
                if not _JOBS:
                    _WAKE.clear()
                    break
                factory, future, timeout = _JOBS.pop(0)
            if future.cancelled():
                continue
            try:
                result = asyncio.run(_await_with_timeout(factory(), timeout))
            except BaseException as exc:
                if not future.cancelled():
                    future.set_exception(exc)
            else:
                if not future.cancelled():
                    future.set_result(result)


async def _await_with_timeout(awaitable: Awaitable[T], timeout: float | None) -> T:
    if timeout is None:
        return await awaitable
    return await asyncio.wait_for(awaitable, timeout=timeout)


def submit_telegram(factory: Callable[[], Awaitable[T]], *, timeout: float | None = 120) -> Future:
    """Queue an async Telegram job onto the dedicated worker thread."""
    _ensure_worker()
    future: Future = Future()
    with _JOBS_LOCK:
        _JOBS.append((factory, future, timeout))  # type: ignore[arg-type]
    _WAKE.set()
    return future


def run_exclusive(factory: Callable[[], Awaitable[T]], *, timeout: float | None = 120) -> T:
    """Blocking helper for non-async callers (pipeline worker thread)."""
    future = submit_telegram(factory, timeout=timeout)
    wait_for = None if timeout is None else timeout + 5
    return future.result(timeout=wait_for)


async def run_exclusive_async(factory: Callable[[], Awaitable[T]], *, timeout: float | None = 120) -> T:
    """Await a Telegram job without blocking the uvicorn event loop."""
    future = submit_telegram(factory, timeout=timeout)
    wait_for = None if timeout is None else timeout + 5

    def wait() -> T:
        return future.result(timeout=wait_for)

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_WAIT_POOL, wait)
