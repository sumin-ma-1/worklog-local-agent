"""Server-side opaque session tokens persisted under data_root."""

from __future__ import annotations

import json
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COOKIE_USER = "worklog_user"
COOKIE_LOGIN = "worklog_login"

# Avoid rewriting web_sessions.json on every authenticated poll.
_LAST_SEEN_PERSIST_EVERY_SEC = 60.0


class SessionStore:
    def __init__(self, data_root: Path) -> None:
        self.path = Path(data_root) / "web_sessions.json"
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, Any]] | None = None
        self._dirty = False
        self._last_persist_at = 0.0

    def _ensure_loaded(self) -> dict[str, dict[str, Any]]:
        if self._data is None:
            self._data = self._read_file()
        return self._data

    def _read_file(self) -> dict[str, dict[str, Any]]:
        if not self.path.is_file():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _write_file(self, data: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def _persist_locked(self, *, force: bool = False) -> None:
        if self._data is None:
            return
        now = time.monotonic()
        if not force and not self._dirty:
            return
        if not force and (now - self._last_persist_at) < _LAST_SEEN_PERSIST_EVERY_SEC:
            return
        self._write_file(self._data)
        self._dirty = False
        self._last_persist_at = now

    def create(self, user_id: int | str, *, name: str | None = None) -> str:
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            data = self._ensure_loaded()
            data[token] = {
                "user_id": str(user_id),
                "name": name,
                "created_at": now,
                "last_seen": now,
            }
            self._dirty = True
            self._persist_locked(force=True)
        return token

    def resolve(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        with self._lock:
            data = self._ensure_loaded()
            meta = data.get(token)
            if not meta:
                return None
            updated = dict(meta)
            updated["last_seen"] = datetime.now(timezone.utc).isoformat()
            data[token] = updated
            self._dirty = True
            # Throttled disk write — polling must not rewrite the file every request.
            self._persist_locked(force=False)
            return updated

    def clear(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            data = self._ensure_loaded()
            if token in data:
                del data[token]
                self._dirty = True
                self._persist_locked(force=True)

    def clear_user(self, user_id: int | str) -> None:
        key = str(user_id)
        with self._lock:
            data = self._ensure_loaded()
            keep = {tok: meta for tok, meta in data.items() if str(meta.get("user_id")) != key}
            if len(keep) != len(data):
                self._data = keep
                self._dirty = True
                self._persist_locked(force=True)

    def latest_seen_by_user(self) -> dict[str, str]:
        latest: dict[str, str] = {}
        with self._lock:
            for meta in self._ensure_loaded().values():
                if not isinstance(meta, dict):
                    continue
                user_id = str(meta.get("user_id") or "")
                seen = str(meta.get("last_seen") or meta.get("created_at") or "")
                if not user_id or not seen:
                    continue
                prev = latest.get(user_id)
                if prev is None or seen > prev:
                    latest[user_id] = seen
        return latest
