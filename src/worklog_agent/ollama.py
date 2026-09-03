from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from worklog_agent.config import AppConfig

logger = logging.getLogger(__name__)

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


class OllamaError(RuntimeError):
    pass


def _host(config: AppConfig) -> str:
    return config.journal.ollama.host.rstrip("/")


def _timeout(config: AppConfig) -> float:
    return config.journal.ollama.timeout_seconds


async def list_model_names(config: AppConfig) -> list[str]:
    url = f"{_host(config)}/api/tags"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(url)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise OllamaError(
            f"로컬 Ollama에 연결하지 못했습니다 ({_host(config)}). "
            "`ollama serve` 가 실행 중인지 확인하세요."
        ) from exc
    payload = response.json()
    models = payload.get("models") or []
    return [str(item.get("name", "")) for item in models if item.get("name")]


def _model_installed(wanted: str, installed: list[str]) -> str | None:
    wanted = wanted.strip()
    if wanted in installed:
        return wanted
    prefix = wanted if ":" in wanted else f"{wanted}:"
    for name in installed:
        if name == wanted or name.startswith(prefix) or name.split(":")[0] == wanted:
            return name
    return None


async def ensure_model(config: AppConfig) -> str:
    wanted = config.journal.ollama.model
    installed = await list_model_names(config)
    matched = _model_installed(wanted, installed)
    if matched:
        if matched != wanted:
            logger.info("Ollama 모델 '%s' 대신 '%s' 를 사용합니다.", wanted, matched)
        return matched
    available = ", ".join(installed) if installed else "(없음)"
    raise OllamaError(
        f"Ollama 모델 '{wanted}' 이(가) 없습니다. `ollama pull {wanted}` 후 다시 실행하세요. "
        f"설치된 모델: {available}"
    )


def _clean_content(text: str) -> str:
    return _THINK_RE.sub("", text).strip()


async def chat(config: AppConfig, system: str, user: str) -> str:
    model = await ensure_model(config)
    url = f"{_host(config)}/api/chat"
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {
            "temperature": config.journal.temperature,
            "num_ctx": config.journal.ollama.num_ctx,
        },
    }
    logger.info("Ollama 호출: %s (%s)", model, _host(config))
    try:
        async with httpx.AsyncClient(timeout=_timeout(config)) as client:
            response = await client.post(url, json=body)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        detail = ""
        if isinstance(exc, httpx.HTTPStatusError):
            detail = f": {exc.response.text[:500]}"
        raise OllamaError(f"Ollama 호출 실패{detail}") from exc

    data = response.json()
    message = data.get("message") or {}
    content = message.get("content") or ""
    cleaned = _clean_content(str(content))
    if not cleaned:
        raise OllamaError(f"Ollama 응답이 비어 있습니다: {data}")
    return cleaned
