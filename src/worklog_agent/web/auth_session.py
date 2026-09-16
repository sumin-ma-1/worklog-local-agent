from __future__ import annotations

import json
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COOKIE_USER = "worklog_user"
COOKIE_LOGIN = "worklog_login"


class SessionStore:
    """Server-side opaque session tokens persisted under data_root."""

    def __init__(self, data_root: Path) -> None:
        self.path = Path(data_root) / "web_sessions.json"
        self._lock = threading.Lock()

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.is_file():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def create(self, user_id: int | str, *, name: str | None = None) -> str:
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            data = self._load()
            data[token] = {
                "user_id": str(user_id),
                "name": name,
                "created_at": now,
                "last_seen": now,
            }
            self._save(data)
        return token

    def resolve(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        with self._lock:
            data = self._load()
            meta = data.get(token)
            if not meta:
                return None
            updated = dict(meta)
            updated["last_seen"] = datetime.now(timezone.utc).isoformat()
            data[token] = updated
            self._save(data)
            return updated

    def clear(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            data = self._load()
            if token in data:
                del data[token]
                self._save(data)

    def clear_user(self, user_id: int | str) -> None:
        key = str(user_id)
        with self._lock:
            data = self._load()
            keep = {tok: meta for tok, meta in data.items() if str(meta.get("user_id")) != key}
            if len(keep) != len(data):
                self._save(keep)

    def latest_seen_by_user(self) -> dict[str, str]:
        latest: dict[str, str] = {}
        with self._lock:
            for meta in self._load().values():
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