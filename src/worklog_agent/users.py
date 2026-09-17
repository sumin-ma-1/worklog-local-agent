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


def share_snapshot_dir(root: Path, token: str) -> Path:
    return Path(root) / "share_snapshots" / token


def normalize_share_mode(mode: str | None) -> str:
    value = str(mode or "live").strip().lower()
    return "snapshot" if value == "snapshot" else "live"


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


def normalize_phone_digits(phone: str | None) -> str:
    return "".join(ch for ch in str(phone or "") if ch.isdigit())


def find_telegram_link_owner(
    data_root: Path,
    session_name: str = "worklog",
    *,
    phone: str | None = None,
    telegram_user_id: int | str | None = None,
    exclude_user_id: int | str | None = None,
) -> str | None:
    """Return another linked user's id if phone or telegram id is already taken."""
    want_phone = normalize_phone_digits(phone)
    want_tg = str(telegram_user_id).strip() if telegram_user_id not in (None, "") else ""
    if not want_phone and not want_tg:
        return None
    exclude = str(exclude_user_id) if exclude_user_id is not None else ""
    base = users_dir(data_root)
    if not base.is_dir():
        return None
    for path in sorted(base.iterdir()):
        if not path.is_dir():
            continue
        other_id = path.name
        if exclude and other_id == exclude:
            continue
        if not telegram_linked(path, session_name):
            continue
        meta = load_user_telegram(path)
        if want_phone and normalize_phone_digits(meta.get("phone")) == want_phone:
            return other_id
        if want_tg and str(meta.get("telegram_user_id") or "").strip() == want_tg:
            return other_id
    return None


def save_user_telegram(root: Path, updates: dict[str, Any]) -> dict[str, Any]:
    current = load_user_telegram(root)
    for key, value in updates.items():
        if value is None:
            current.pop(key, None)
        else:
            current[key] = value
    _write_json(telegram_json_path(root), current)
    return current


def bot_link_tokens_path(data_root: Path) -> Path:
    return Path(data_root) / "bot_link_tokens.json"


def _load_bot_link_tokens(data_root: Path) -> dict[str, Any]:
    raw = _read_json(bot_link_tokens_path(data_root), {"tokens": {}})
    if not isinstance(raw, dict):
        return {"tokens": {}}
    tokens = raw.get("tokens")
    if not isinstance(tokens, dict):
        tokens = {}
    return {"tokens": tokens}


def _save_bot_link_tokens(data_root: Path, payload: dict[str, Any]) -> None:
    _write_json(bot_link_tokens_path(data_root), payload)


def create_bot_link_token(data_root: Path, user_id: int | str, *, ttl_seconds: int = 3600) -> str:
    """Create a short-lived deep-link token for t.me/Bot?start=<token>."""
    payload = _load_bot_link_tokens(data_root)
    tokens: dict[str, Any] = dict(payload.get("tokens") or {})
    # Drop expired
    now = datetime.now(timezone.utc)
    cleaned: dict[str, Any] = {}
    for key, item in tokens.items():
        if not isinstance(item, dict):
            continue
        exp = str(item.get("expires_at") or "")
        try:
            if datetime.fromisoformat(exp) > now:
                cleaned[key] = item
        except ValueError:
            continue
    token = secrets.token_urlsafe(16).replace("-", "").replace("_", "")[:24]
    expires = now.timestamp() + max(60, int(ttl_seconds))
    cleaned[token] = {
        "user_id": str(user_id),
        "expires_at": datetime.fromtimestamp(expires, tz=timezone.utc).isoformat(),
    }
    _save_bot_link_tokens(data_root, {"tokens": cleaned})
    return token


def consume_bot_link_token(data_root: Path, token: str) -> str | None:
    """Return dashboard user_id for a valid token and invalidate it."""
    raw = str(token or "").strip()
    if not raw:
        return None
    payload = _load_bot_link_tokens(data_root)
    tokens: dict[str, Any] = dict(payload.get("tokens") or {})
    item = tokens.pop(raw, None)
    _save_bot_link_tokens(data_root, {"tokens": tokens})
    if not isinstance(item, dict):
        return None
    exp = str(item.get("expires_at") or "")
    try:
        if datetime.fromisoformat(exp) <= datetime.now(timezone.utc):
            return None
    except ValueError:
        return None
    user_id = str(item.get("user_id") or "").strip()
    return user_id or None


def resolve_account_by_telegram_id(data_root: Path, telegram_user_id: int | str) -> str | None:
    """Find dashboard user id whose telegram.json.telegram_user_id matches."""
    want = str(telegram_user_id).strip()
    if not want:
        return None
    base = users_dir(data_root)
    if not base.is_dir():
        return None
    for path in sorted(base.iterdir()):
        if not path.is_dir():
            continue
        meta = load_user_telegram(path)
        if str(meta.get("telegram_user_id") or "").strip() == want:
            return path.name
    return None


def mark_bot_linked(
    data_root: Path,
    user_id: int | str,
    *,
    telegram_user_id: int | str,
    chat_id: int | str | None = None,
) -> dict[str, Any]:
    root = ensure_user_root(data_root, user_id)
    updates: dict[str, Any] = {
        "telegram_user_id": int(telegram_user_id)
        if str(telegram_user_id).lstrip("-").isdigit()
        else str(telegram_user_id),
        "bot_linked_at": datetime.now(timezone.utc).isoformat(),
    }
    if chat_id is not None:
        updates["bot_chat_id"] = int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id
    return save_user_telegram(root, updates)


def is_bot_linked(root: Path) -> bool:
    meta = load_user_telegram(root)
    return bool(meta.get("bot_linked_at") or meta.get("bot_chat_id"))


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


def _remove_share_token(root: Path, shares: dict[str, dict[str, Any]], token: str) -> None:
    shares.pop(token, None)
    snap = share_snapshot_dir(root, token)
    if snap.exists():
        shutil.rmtree(snap, ignore_errors=True)


def _copy_day_attachments(storage: Any, day: str, files_root: Path) -> list[dict[str, str]]:
    copied: list[dict[str, str]] = []
    for file in storage.list_attachments(day):
        rel = str(file.get("relative") or "").replace("\\", "/").lstrip("/")
        if not rel:
            continue
        try:
            src = storage.attachment_file(rel)
        except (ValueError, FileNotFoundError):
            continue
        dest = files_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        copied.append(
            {
                "name": str(file.get("name") or dest.name),
                "relative": rel,
                "day": str(file.get("day") or day),
                "chat": str(file.get("chat") or ""),
            }
        )
    return copied


def _day_share_markdown(storage: Any, day: str) -> tuple[bool, str]:
    has_any = storage.has_any_journal(day) if hasattr(storage, "has_any_journal") else storage.journal_path(day).exists()
    if storage.journal_path(day).exists():
        return True, storage.read_journal(day)
    rooms = storage.list_room_journals(day) if hasattr(storage, "list_room_journals") else []
    chunks: list[str] = []
    for room in rooms:
        try:
            text = storage.read_room_journal(day, room["id"]).strip()
        except FileNotFoundError:
            continue
        if text:
            chunks.append(f"# {room['title']}\n\n{text}")
    if chunks:
        return True, "\n\n".join(chunks) + "\n"
    return has_any, ""


def attachments_for_view(
    storage: Any | None,
    day: str,
    *,
    view: str,
    rooms: list[dict[str, str]] | None = None,
    daily: dict[str, Any] | None = None,
    files: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    from worklog_agent.storage import slugify

    if rooms is not None:
        room_rows = rooms
    elif storage is not None and hasattr(storage, "list_room_journals"):
        room_rows = storage.list_room_journals(day)
    else:
        room_rows = []

    if files is not None:
        source_files = files
    elif storage is not None:
        source_files = storage.list_attachments(day)
    else:
        source_files = []
    slug_to_title: dict[str, str] = {}
    id_to_slug: dict[str, str] = {}
    for room in room_rows:
        title = str(room.get("title") or room.get("id") or "")
        slug = slugify(title, fallback=str(room.get("id") or "chat"))
        slug_to_title[slug] = title
        id_to_slug[str(room.get("id"))] = slug
    if isinstance(daily, dict):
        for chat in daily.get("chats") or []:
            if not isinstance(chat, dict):
                continue
            chat_id = str(chat.get("chat_id") or "")
            title = str(chat.get("title") or chat_id or "chat")
            slug = slugify(title, fallback=chat_id or "chat")
            slug_to_title.setdefault(slug, title)
            if chat_id:
                id_to_slug.setdefault(chat_id, slug)

    enriched: list[dict[str, str]] = []
    for item in source_files:
        chat_slug = str(item.get("chat") or item.get("chat_slug") or "")
        chat_title = str(item.get("chat_title") or "") or slug_to_title.get(chat_slug) or chat_slug
        row = dict(item)
        row["chat_slug"] = chat_slug
        row["chat_title"] = chat_title
        enriched.append(row)

    selected = str(view or "all").strip() or "all"
    if selected != "all":
        want_slug = id_to_slug.get(selected)
        if not want_slug:
            title = next((r.get("title") for r in room_rows if str(r.get("id")) == selected), selected)
            want_slug = slugify(str(title or selected), fallback=str(selected))
        enriched = [row for row in enriched if row.get("chat_slug") == want_slug]
    return enriched


def journal_day_payload(storage: Any, day: str, *, view: str | None = None) -> dict[str, Any]:
    rooms = storage.list_room_journals(day) if hasattr(storage, "list_room_journals") else []
    has_combined = storage.journal_path(day).exists()
    has_journal = (
        storage.has_any_journal(day)
        if hasattr(storage, "has_any_journal")
        else (has_combined or bool(rooms))
    )
    views: list[dict[str, str]] = []
    if has_combined:
        views.append({"id": "all", "label": "통합", "kind": "combined"})
    for room in rooms:
        views.append({"id": str(room["id"]), "label": str(room["title"]), "kind": "room"})

    selected = str(view or "").strip()
    if not selected:
        selected = views[0]["id"] if views else "all"
    valid_ids = {item["id"] for item in views}
    if selected not in valid_ids:
        if selected == "all" and not has_combined and rooms:
            selected = str(rooms[0]["id"])
        elif views:
            selected = views[0]["id"]
        else:
            selected = "all"

    markdown = ""
    if selected == "all" and has_combined:
        markdown = storage.read_journal(day)
    elif selected != "all":
        try:
            markdown = storage.read_room_journal(day, selected)
        except FileNotFoundError:
            markdown = ""

    daily_payload = None
    if storage.daily_path(day).exists():
        daily_payload = storage.load_daily(day).model_dump(mode="json")
    attachments = attachments_for_view(
        storage,
        day,
        view=selected,
        rooms=rooms,
        daily=daily_payload,
    )
    return {
        "date": day,
        "markdown": markdown,
        "has_journal": has_journal,
        "has_combined": has_combined,
        "view": selected,
        "views": views,
        "daily": daily_payload,
        "attachments": attachments,
    }


def pack_day_share_snapshot(storage: Any, day: str, attachments: list[dict[str, str]]) -> dict[str, Any]:
    """Store combined + per-room markdown so shared viewers can switch views."""
    rooms = storage.list_room_journals(day) if hasattr(storage, "list_room_journals") else []
    has_combined = storage.journal_path(day).exists()
    has_journal = (
        storage.has_any_journal(day)
        if hasattr(storage, "has_any_journal")
        else (has_combined or bool(rooms))
    )
    views: list[dict[str, str]] = []
    if has_combined:
        views.append({"id": "all", "label": "통합", "kind": "combined"})
    room_markdowns: dict[str, str] = {}
    for room in rooms:
        room_id = str(room["id"])
        views.append({"id": room_id, "label": str(room["title"]), "kind": "room"})
        try:
            room_markdowns[room_id] = storage.read_room_journal(day, room_id)
        except FileNotFoundError:
            room_markdowns[room_id] = ""
    combined_markdown = storage.read_journal(day) if has_combined else ""
    daily_payload = None
    if storage.daily_path(day).exists():
        daily_payload = storage.load_daily(day).model_dump(mode="json")
    enriched_attachments = attachments_for_view(
        storage,
        day,
        view="all",
        rooms=rooms,
        daily=daily_payload,
        files=attachments,
    )
    default_view = "all" if has_combined else (views[0]["id"] if views else "all")
    markdown = combined_markdown
    if default_view != "all":
        markdown = room_markdowns.get(default_view, "")
    return {
        "date": day,
        "markdown": markdown,
        "combined_markdown": combined_markdown,
        "room_markdowns": room_markdowns,
        "has_journal": has_journal,
        "has_combined": has_combined,
        "views": views,
        "view": default_view,
        "attachments": enriched_attachments,
        "daily": None,
    }


def select_share_day_view(packed: dict[str, Any], view: str | None = None) -> dict[str, Any]:
    views = [item for item in (packed.get("views") or []) if isinstance(item, dict)]
    has_combined = bool(packed.get("has_combined"))
    room_markdowns = packed.get("room_markdowns") if isinstance(packed.get("room_markdowns"), dict) else {}
    selected = str(view or packed.get("view") or "").strip()
    if not selected:
        selected = str(views[0]["id"]) if views else "all"
    valid_ids = {str(item.get("id")) for item in views}
    if selected not in valid_ids:
        if selected == "all" and not has_combined and views:
            selected = str(views[0]["id"])
        elif views:
            selected = str(views[0]["id"])
        else:
            selected = "all"

    if selected == "all":
        markdown = str(packed.get("combined_markdown") or packed.get("markdown") or "")
    else:
        markdown = str(room_markdowns.get(selected) or "")
        if not markdown and not room_markdowns:
            # Legacy snapshots only stored one markdown blob.
            markdown = str(packed.get("markdown") or "")

    rooms = [
        {"id": str(item.get("id")), "title": str(item.get("label") or item.get("id"))}
        for item in views
        if item.get("kind") == "room"
    ]
    attachments = attachments_for_view(
        storage=None,  # unused when files provided
        day=str(packed.get("date") or ""),
        view=selected,
        rooms=rooms,
        daily=None,
        files=list(packed.get("attachments") or []),
    ) if packed.get("attachments") is not None else []
    # attachments_for_view with storage=None - need to fix to not call storage methods when files provided
    return {
        "date": str(packed.get("date") or ""),
        "markdown": markdown,
        "has_journal": bool(packed.get("has_journal") or markdown),
        "has_combined": has_combined,
        "view": selected,
        "views": views,
        "attachments": attachments,
        "daily": packed.get("daily"),
    }


def build_day_share_snapshot(storage: Any, day: str, token: str) -> dict[str, Any]:
    snap_root = share_snapshot_dir(storage.root, token)
    if snap_root.exists():
        shutil.rmtree(snap_root, ignore_errors=True)
    files_root = snap_root / "files"
    files_root.mkdir(parents=True, exist_ok=True)
    attachments = _copy_day_attachments(storage, day, files_root)
    return pack_day_share_snapshot(storage, day, attachments)


def create_share_token(root: Path, day: str, *, mode: str = "live") -> dict[str, Any]:
    from worklog_agent.storage import Storage

    mode = normalize_share_mode(mode)
    shares = load_shares(root)
    # Reuse the same token for day+mode so re-sharing does not break links already sent.
    existing: str | None = None
    for token, meta in shares.items():
        if meta.get("day") == day and normalize_share_mode(meta.get("mode")) == mode:
            existing = token
            break
    token = existing or secrets.token_urlsafe(24)
    prev = shares.get(token) if existing else None
    meta: dict[str, Any] = {
        "day": day,
        "scope": "day",
        "mode": mode,
        "created_at": (prev or {}).get("created_at") or datetime.now(timezone.utc).isoformat(),
    }
    if mode == "snapshot":
        meta["snapshot"] = build_day_share_snapshot(Storage(root), day, token)
    shares[token] = meta
    save_shares(root, shares)
    return {"token": token, **meta}


def create_library_share_token(root: Path, *, mode: str = "live") -> dict[str, Any]:
    from worklog_agent.storage import Storage

    mode = normalize_share_mode(mode)
    shares = load_shares(root)
    # Reuse the same token for library+mode so re-sharing does not break links already sent.
    existing: str | None = None
    for token, meta in shares.items():
        if meta.get("scope") == "library" and normalize_share_mode(meta.get("mode")) == mode:
            existing = token
            break
    token = existing or secrets.token_urlsafe(24)
    prev = shares.get(token) if existing else None
    meta: dict[str, Any] = {
        "scope": "library",
        "mode": mode,
        "created_at": (prev or {}).get("created_at") or datetime.now(timezone.utc).isoformat(),
    }
    if mode == "snapshot":
        storage = Storage(root)
        dates = sorted(set(storage.list_journal_dates()) | set(storage.list_daily_dates()), reverse=True)
        snap_root = share_snapshot_dir(root, token)
        if snap_root.exists():
            shutil.rmtree(snap_root, ignore_errors=True)
        files_root = snap_root / "files"
        files_root.mkdir(parents=True, exist_ok=True)
        packed = []
        for day in dates:
            attachments = _copy_day_attachments(storage, day, files_root)
            packed.append(pack_day_share_snapshot(storage, day, attachments))
        meta["snapshot"] = {"journals": packed}
    shares[token] = meta
    save_shares(root, shares)
    return {"token": token, **meta}


def library_share_for(root: Path, *, mode: str | None = None) -> dict[str, Any] | None:
    want = normalize_share_mode(mode) if mode else None
    for token, meta in load_shares(root).items():
        if meta.get("scope") != "library":
            continue
        if want and normalize_share_mode(meta.get("mode")) != want:
            continue
        return {"token": token, **meta}
    return None


def revoke_share_for_day(root: Path, day: str, *, live_only: bool = True) -> int:
    shares = load_shares(root)
    removed = 0
    for token, meta in list(shares.items()):
        if meta.get("day") != day:
            continue
        if live_only and normalize_share_mode(meta.get("mode")) == "snapshot":
            continue
        _remove_share_token(root, shares, token)
        removed += 1
    if removed:
        save_shares(root, shares)
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


def share_for_day(root: Path, day: str, *, mode: str | None = None) -> dict[str, Any] | None:
    want = normalize_share_mode(mode) if mode else None
    for token, meta in load_shares(root).items():
        if meta.get("day") != day:
            continue
        if want and normalize_share_mode(meta.get("mode")) != want:
            continue
        return {"token": token, **meta}
    return None


def snapshot_day_payload(
    meta: dict[str, Any],
    day: str | None = None,
    *,
    view: str | None = None,
) -> dict[str, Any] | None:
    snap = meta.get("snapshot")
    if not isinstance(snap, dict):
        return None
    if meta.get("scope") == "library":
        target = str(day or "")
        journals = snap.get("journals")
        if not isinstance(journals, list):
            return None
        for item in journals:
            if isinstance(item, dict) and str(item.get("date") or "") == target:
                return select_share_day_view(item, view)
        return None
    return select_share_day_view(snap, view)


def snapshot_library_index(meta: dict[str, Any]) -> list[dict[str, Any]]:
    snap = meta.get("snapshot")
    if not isinstance(snap, dict):
        return []
    journals = snap.get("journals")
    if not isinstance(journals, list):
        return []
    items = []
    for item in journals:
        if not isinstance(item, dict):
            continue
        day = str(item.get("date") or "")
        if not day:
            continue
        attachments = item.get("attachments") if isinstance(item.get("attachments"), list) else []
        items.append(
            {
                "date": day,
                "has_journal": bool(item.get("has_journal") or item.get("markdown")),
                "attachments": len(attachments),
            }
        )
    items.sort(key=lambda row: str(row.get("date") or ""), reverse=True)
    return items


def snapshot_attachment_file(root: Path, token: str, relative: str) -> Path:
    rel = relative.replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/"):
        raise ValueError("첨부파일 경로가 올바르지 않습니다.")
    base = (share_snapshot_dir(root, token) / "files").resolve()
    path = (base / rel).resolve()
    if path != base and base not in path.parents:
        raise ValueError("첨부파일 경로가 저장소 밖입니다.")
    if not path.is_file():
        raise FileNotFoundError(f"첨부파일이 없습니다: {relative}")
    return path


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
