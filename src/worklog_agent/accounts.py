from __future__ import annotations

import json
import re
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import bcrypt

_USERNAME_RE = re.compile(r"^[a-z0-9_]{3,32}$")
_MIN_PASSWORD = 8
_ADMIN_USERNAMES = frozenset({"devsm"})


def is_admin_username(username: str | None) -> bool:
    if not username:
        return False
    return AccountStore.normalize_username(username) in _ADMIN_USERNAMES


class AccountError(ValueError):
    pass


class AccountStore:
    def __init__(self, data_root: Path) -> None:
        self.path = Path(data_root) / "accounts.json"
        self._lock = threading.Lock()

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"users": []}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"users": []}
        if not isinstance(raw, dict):
            return {"users": []}
        users = raw.get("users")
        if not isinstance(users, list):
            users = []
        return {"users": users}

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def normalize_username(username: str) -> str:
        return username.strip().lower()

    @classmethod
    def validate_username(cls, username: str) -> str:
        normalized = cls.normalize_username(username)
        if not _USERNAME_RE.fullmatch(normalized):
            raise AccountError("아이디는 영문 소문자·숫자·밑줄 3~32자여야 합니다.")
        return normalized

    @staticmethod
    def validate_password(password: str) -> str:
        if len(password) < _MIN_PASSWORD:
            raise AccountError(f"비밀번호는 {_MIN_PASSWORD}자 이상이어야 합니다.")
        return password

    @staticmethod
    def _hash_password(password: str) -> str:
        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    @staticmethod
    def _check_password(password: str, password_hash: str) -> bool:
        try:
            return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
        except ValueError:
            return False

    def get(self, user_id: str) -> dict[str, Any] | None:
        with self._lock:
            for user in self._load()["users"]:
                if str(user.get("id")) == str(user_id):
                    return dict(user)
        return None

    def find_by_username(self, username: str) -> dict[str, Any] | None:
        key = self.normalize_username(username)
        with self._lock:
            for user in self._load()["users"]:
                if str(user.get("username")) == key:
                    return dict(user)
        return None

    def register(self, username: str, password: str) -> dict[str, Any]:
        username = self.validate_username(username)
        password = self.validate_password(password)
        with self._lock:
            data = self._load()
            if any(str(u.get("username")) == username for u in data["users"]):
                raise AccountError("이미 사용 중인 아이디입니다.")
            user = {
                "id": secrets.token_urlsafe(12),
                "username": username,
                "password_hash": self._hash_password(password),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            data["users"].append(user)
            self._save(data)
            return {"id": user["id"], "username": user["username"], "created_at": user["created_at"]}

    def authenticate(self, username: str, password: str) -> dict[str, Any]:
        user = self.find_by_username(username)
        if not user or not self._check_password(password, str(user.get("password_hash") or "")):
            raise AccountError("아이디 또는 비밀번호가 올바르지 않습니다.")
        return {"id": user["id"], "username": user["username"], "created_at": user.get("created_at")}

    def list_public(self) -> list[dict[str, Any]]:
        with self._lock:
            users = [self.public_user(user) for user in self._load()["users"]]
        users.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return users

    def delete(self, user_id: str) -> bool:
        target = str(user_id)
        with self._lock:
            data = self._load()
            keep = [user for user in data["users"] if str(user.get("id")) != target]
            if len(keep) == len(data["users"]):
                return False
            data["users"] = keep
            self._save(data)
            return True

    def public_user(self, user: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": user["id"],
            "username": user["username"],
            "created_at": user.get("created_at"),
        }
