from __future__ import annotations

import json
import logging
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from worklog_agent.config import AppConfig, normalize_chat_ref

logger = logging.getLogger(__name__)

LEGACY_DIRS = ("sessions", "raw", "attachments", "daily", "journals")
LEGACY_FILES = ("chat_titles.json",)
MIGRATION_FLAG = ".multiuser_migrated"


def users_dir(data_root: Path) -> Path:
    return Path(data_root) / "users"


def pending_dir(data_root: Path) -> Path:
    return Path(data_root) / "pending"


def user_root(data_root: Path, user_id: int | str) -> Path:
    return users_dir(data_root) / str(user_id)


def pending_root(data_root: Path, login_id: str) -> Path:
    return pending_dir(data_root) / login_id


def ensure_user_root(data_root: Path, user_id: int | str) -> Path:
    root = user_root(data_root, user_id)
    root.mkdir(parents=True, exist_ok=True)
    return root


def delete_user_data(data_root: Path, user_id: int | str) -> bool:
    root = user_root(data_root, user_id)
    if not root.exists():
        return False
    shutil.rmtree(root, ignore_errors=True)
    return not root.exists()


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def chats_path(root: Path) -> Path:
    return Path(root) / "chats.json"


def shares_path(root: Path) -> Path:
    return Path(root) / "shares.json"


def load_user_chats(root: Path) -> list[str | int]:
    raw = _read_json(chats_path(root), {"chats": []})
    items = raw.get("chats", []) if isinstance(raw, dict) else []
    out: list[str | int] = []
    for item in items:
        try:
            out.append(normalize_chat_ref(item))
        except ValueError:
            continue
    return out


def save_user_chats(root: Path, chats: list[str | int]) -> list[str | int]:
    normalized = [normalize_chat_ref(item) for item in chats]
    _write_json(chats_path(root), {"chats": normalized})
    return normalized


def add_user_chat(root: Path, value: str | int) -> list[str | int]:
    ref = normalize_chat_ref(value)
    chats = load_user_chats(root)
    keys = {str(item) for item in chats}
    if str(ref) in keys:
        raise ValueError(f"이미 등록된 채팅방입니다: {ref}")
    chats.append(ref)
    return save_user_chats(root, chats)


def remove_user_chat(root: Path, value: str | int) -> list[str | int]:
    key = str(normalize_chat_ref(value))
    chats = load_user_chats(root)
    remaining = [item for item in chats if str(item) != key]
    if len(remaining) == len(chats):
        raise ValueError(f"등록되지 않은 채팅방입니다: {value}")
    return save_user_chats(root, remaining)


def telegram_json_path(root: Path) -> Path:
    return Path(root) / "telegram.json"


def preferences_path(root: Path) -> Path:
    return Path(root) / "preferences.json"


def load_user_preferences(root: Path) -> dict[str, Any]:
    raw = _read_json(preferences_path(root), {})
    return raw if isinstance(raw, dict) else {}


def save_user_preferences(root: Path, updates: dict[str, Any]) -> dict[str, Any]:
    current = load_user_preferences(root)
    for key, value in updates.items():
        if value is None:
            current.pop(key, None)
        else:
            current[key] = value
    _write_json(preferences_path(root), current)
    return current


def user_config(base: AppConfig, user_id: int | str) -> AppConfig:
    """Return a config copy scoped to the user's storage root, chats, and phone."""
    root = ensure_user_root(base.data_root, user_id)
    cfg = base.model_copy(deep=True)
    cfg.storage.root = root
    cfg.telegram.chats = load_user_chats(root)
    phone = load_user_telegram(root).get("phone")
    if phone:
        cfg.env.telegram_phone = str(phone)
    prefs = load_user_preferences(root)
    timezone = str(prefs.get("timezone") or "").strip()
    if timezone:
        cfg.timezone = timezone
    model = str(prefs.get("model") or "").strip()
    if model:
        cfg.journal.ollama.model = model
    return cfg


def load_user_telegram(root: Path) -> dict[str, Any]:
    raw = _read_json(telegram_json_path(root), {})
    return raw if isinstance(raw, dict) else {}


def save_user_telegram(root: Path, updates: dict[str, Any]) -> dict[str, Any]:
    current = load_user_telegram(root)
    for key, value in updates.items():
        if value is None:
            current.pop(key, None)
        else:
            current[key] = value
    _write_json(telegram_json_path(root), current)
    return current


def telegram_session_file(root: Path, session_name: str = "worklog") -> Path:
    return Path(root) / "sessions" / f"{session_name}.session"


def telegram_linked(root: Path, session_name: str = "worklog") -> bool:
    return telegram_session_file(root, session_name).is_file()


def pending_config(base: AppConfig, login_id: str) -> AppConfig:
    root = pending_root(base.data_root, login_id)
    root.mkdir(parents=True, exist_ok=True)
    cfg = base.model_copy(deep=True)
    cfg.storage.root = root
    cfg.telegram.chats = []
    return cfg


def move_pending_to_user(data_root: Path, login_id: str, user_id: int | str) -> Path:
    src = pending_root(data_root, login_id)
    dest = ensure_user_root(data_root, user_id)
    if src.is_dir():
        for child in src.iterdir():
            target = dest / child.name
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            shutil.move(str(child), str(target))
        shutil.rmtree(src, ignore_errors=True)
    return dest


def load_shares(root: Path) -> dict[str, dict[str, Any]]:
    raw = _read_json(shares_path(root), {})
    return raw if isinstance(raw, dict) else {}


def save_shares(root: Path, shares: dict[str, dict[str, Any]]) -> None:
    _write_json(shares_path(root), shares)


def create_share_token(root: Path, day: str) -> dict[str, Any]:
    shares = load_shares(root)
    # One active token per day: drop previous tokens for same day.
    shares = {token: meta for token, meta in shares.items() if meta.get("day") != day}
    token = secrets.token_urlsafe(24)
    meta = {
        "day": day,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    shares[token] = meta
    save_shares(root, shares)
    return {"token": token, **meta}


def revoke_share_for_day(root: Path, day: str) -> int:
    shares = load_shares(root)
    keep = {token: meta for token, meta in shares.items() if meta.get("day") != day}
    removed = len(shares) - len(keep)
    if removed:
        save_shares(root, keep)
    return removed


def find_share(data_root: Path, token: str) -> tuple[Path, dict[str, Any]] | None:
    base = users_dir(data_root)
    if not base.is_dir():
        return None
    for user_dir in base.iterdir():
        if not user_dir.is_dir():
            continue
        shares = load_shares(user_dir)
        meta = shares.get(token)
        if meta:
            return user_dir, meta
    return None


def share_for_day(root: Path, day: str) -> dict[str, Any] | None:
    for token, meta in load_shares(root).items():
        if meta.get("day") == day:
            return {"token": token, **meta}
    return None


def has_legacy_layout(data_root: Path) -> bool:
    root = Path(data_root)
    if (root / MIGRATION_FLAG).is_file():
        return False
    if users_dir(root).is_dir() and any(users_dir(root).iterdir()):
        return False
    for name in LEGACY_DIRS:
        path = root / name
        if path.is_dir() and any(path.iterdir()):
            return True
    for name in LEGACY_FILES:
        if (root / name).is_file():
            return True
    return False


def migrate_legacy_to_user(data_root: Path, user_id: int | str, chats: list[str | int] | None = None) -> bool:
    """Move legacy single-tenant data into users/<id>. Returns True if migrated."""
    root = Path(data_root)
    if not has_legacy_layout(root):
        return False
    dest = ensure_user_root(root, user_id)
    logger.info("레거시 데이터를 사용자 %s 로 이전합니다: %s -> %s", user_id, root, dest)
    for name in LEGACY_DIRS:
        src = root / name
        if not src.exists():
            continue
        target = dest / name
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        shutil.move(str(src), str(target))
    for name in LEGACY_FILES:
        src = root / name
        if src.is_file():
            target = dest / name
            if target.exists():
                target.unlink()
            shutil.move(str(src), str(target))
    if chats is not None:
        save_user_chats(dest, chats)
    (root / MIGRATION_FLAG).write_text(
        json.dumps({"user_id": str(user_id), "migrated_at": datetime.now(timezone.utc).isoformat()}),
        encoding="utf-8",
    )
    return True
