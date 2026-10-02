from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Iterable

from worklog_agent.models import ChatState, DailyBundle, MessageRecord


def slugify(value: str, fallback: str = "chat") -> str:
    text = re.sub(r"[^\w\s\-\.가-힣]+", "", value, flags=re.UNICODE).strip()
    text = re.sub(r"[\s]+", "_", text)
    return text[:80] or fallback


class Storage:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.sessions = root / "sessions"
        self.raw = root / "raw"
        self.attachments = root / "attachments"
        self.daily = root / "daily"
        self.journals = root / "journals"

    def ensure(self) -> None:
        for path in (
            self.sessions,
            self.raw,
            self.attachments,
            self.daily,
            self.journals,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def session_path(self, name: str) -> Path:
        self.sessions.mkdir(parents=True, exist_ok=True)
        return self.sessions / name

    def chat_dir(self, chat_id: int) -> Path:
        path = self.raw / str(chat_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def messages_path(self, chat_id: int) -> Path:
        return self.chat_dir(chat_id) / "messages.jsonl"

    def state_path(self, chat_id: int) -> Path:
        return self.chat_dir(chat_id) / "state.json"

    def daily_path(self, day: str) -> Path:
        return self.daily / f"{day}.json"

    def journal_path(self, day: str) -> Path:
        return self.journals / f"{day}.md"

    def journal_rooms_dir(self, day: str) -> Path:
        return self.journals / f"{day}.rooms"

    def room_journal_path(self, day: str, chat_id: str | int) -> Path:
        return self.journal_rooms_dir(day) / f"{chat_id}.md"

    def room_journals_index_path(self, day: str) -> Path:
        return self.journal_rooms_dir(day) / "index.json"

    def attachment_dir(self, day: str, chat_title: str) -> Path:
        path = self.attachments / day / slugify(chat_title, fallback="chat")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def load_state(self, chat_id: int, title: str) -> ChatState:
        path = self.state_path(chat_id)
        if not path.exists():
            return ChatState(chat_id=chat_id, title=title)
        return ChatState.model_validate_json(path.read_text(encoding="utf-8"))

    def save_state(self, state: ChatState) -> None:
        self.state_path(state.chat_id).write_text(
            state.model_dump_json(indent=2),
            encoding="utf-8",
        )

    def read_messages(self, chat_id: int) -> list[MessageRecord]:
        path = self.messages_path(chat_id)
        if not path.exists():
            return []
        records: list[MessageRecord] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                records.append(MessageRecord.model_validate_json(line))
        return records

    def write_messages(self, chat_id: int, records: Iterable[MessageRecord]) -> None:
        path = self.messages_path(chat_id)
        ordered = sorted(records, key=lambda item: item.id)
        lines = [record.model_dump_json() for record in ordered]
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    def append_messages(self, chat_id: int, records: list[MessageRecord]) -> None:
        if not records:
            return
        existing = {item.id: item for item in self.read_messages(chat_id)}
        for record in records:
            existing[record.id] = record
        ordered = sorted(existing.values(), key=lambda item: item.id)
        self.write_messages(chat_id, ordered)

    def iter_chat_ids(self) -> list[int]:
        if not self.raw.exists():
            return []
        ids: list[int] = []
        for path in self.raw.iterdir():
            if path.is_dir() and path.name.lstrip("-").isdigit():
                ids.append(int(path.name))
        return sorted(ids)

    def load_all_messages(self) -> list[MessageRecord]:
        messages: list[MessageRecord] = []
        for chat_id in self.iter_chat_ids():
            messages.extend(self.read_messages(chat_id))
        return messages

    def save_daily(self, bundle: DailyBundle) -> Path:
        self.daily.mkdir(parents=True, exist_ok=True)
        path = self.daily_path(bundle.date)
        path.write_text(bundle.model_dump_json(indent=2), encoding="utf-8")
        return path

    def load_daily(self, day: str) -> DailyBundle:
        path = self.daily_path(day)
        if not path.exists():
            raise FileNotFoundError(f"날짜별 정리본이 없습니다: {path}")
        return DailyBundle.model_validate_json(path.read_text(encoding="utf-8"))

    def save_journal(self, day: str, markdown: str) -> Path:
        self.journals.mkdir(parents=True, exist_ok=True)
        path = self.journal_path(day)
        path.write_text(markdown.rstrip() + "\n", encoding="utf-8")
        return path

    def save_room_journal(self, day: str, chat_id: str | int, markdown: str) -> Path:
        root = self.journal_rooms_dir(day)
        root.mkdir(parents=True, exist_ok=True)
        path = self.room_journal_path(day, chat_id)
        path.write_text(markdown.rstrip() + "\n", encoding="utf-8")
        return path

    def write_room_journals_index(self, day: str, rooms: list[dict[str, str]]) -> Path:
        root = self.journal_rooms_dir(day)
        root.mkdir(parents=True, exist_ok=True)
        path = self.room_journals_index_path(day)
        path.write_text(
            json.dumps({"rooms": rooms}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def list_journal_dates(self) -> list[str]:
        if not self.journals.exists():
            return []
        dates: set[str] = set()
        for path in self.journals.glob("*.md"):
            dates.add(path.stem)
        for path in self.journals.glob("*.rooms"):
            if path.is_dir():
                dates.add(path.name[: -len(".rooms")])
        return sorted(dates, reverse=True)

    def has_any_journal(self, day: str) -> bool:
        if self.journal_path(day).exists():
            return True
        rooms = self.journal_rooms_dir(day)
        if not rooms.is_dir():
            return False
        return any(path.suffix == ".md" and path.is_file() for path in rooms.iterdir())

    def list_room_journals(self, day: str) -> list[dict[str, str]]:
        root = self.journal_rooms_dir(day)
        if not root.is_dir():
            return []
        titles: dict[str, str] = {}
        index_path = self.room_journals_index_path(day)
        if index_path.is_file():
            try:
                payload = json.loads(index_path.read_text(encoding="utf-8"))
                for item in payload.get("rooms") or []:
                    if isinstance(item, dict) and item.get("id") is not None:
                        titles[str(item["id"])] = str(item.get("title") or item["id"])
            except (json.JSONDecodeError, OSError, TypeError):
                titles = {}
        chat_titles = self.load_chat_titles()
        rooms: list[dict[str, str]] = []
        for path in sorted(root.glob("*.md"), key=lambda p: p.stem):
            chat_id = path.stem
            title = titles.get(chat_id) or chat_titles.get(chat_id) or chat_id
            rooms.append({"id": chat_id, "title": title})
        return rooms

    def read_journal(self, day: str) -> str:
        path = self.journal_path(day)
        if not path.exists():
            raise FileNotFoundError(f"일지가 없습니다: {path}")
        return path.read_text(encoding="utf-8")

    def read_room_journal(self, day: str, chat_id: str | int) -> str:
        path = self.room_journal_path(day, chat_id)
        if not path.exists():
            raise FileNotFoundError(f"방 일지가 없습니다: {path}")
        return path.read_text(encoding="utf-8")

    def clear_room_journals(self, day: str) -> bool:
        root = self.journal_rooms_dir(day)
        if not root.exists():
            return False
        shutil.rmtree(root, ignore_errors=True)
        return not root.exists()

    def delete_journal(self, day: str) -> bool:
        """Remove journal markdown, room journals, daily bundle, and day attachments."""
        removed = False
        path = self.journal_path(day)
        if path.exists():
            path.unlink()
            removed = True
        if self.clear_room_journals(day):
            removed = True
        daily = self.daily_path(day)
        if daily.exists():
            daily.unlink(missing_ok=True)
            removed = True
        attach_dir = self.attachments / day
        if attach_dir.exists():
            shutil.rmtree(attach_dir, ignore_errors=True)
            if not attach_dir.exists():
                removed = True
        return removed

    def list_daily_dates(self) -> list[str]:
        if not self.daily.exists():
            return []
        return sorted((path.stem for path in self.daily.glob("*.json")), reverse=True)

    def list_attachment_dates(self) -> list[str]:
        if not self.attachments.exists():
            return []
        days: list[str] = []
        for path in self.attachments.iterdir():
            if path.is_dir() and len(path.name) == 10 and path.name[4] == "-" and path.name[7] == "-":
                days.append(path.name)
        return sorted(days, reverse=True)

    def list_day_dates(self) -> list[str]:
        """Dates that have a journal, daily bundle, or attachments."""
        return sorted(
            set(self.list_journal_dates())
            | set(self.list_daily_dates())
            | set(self.list_attachment_dates()),
            reverse=True,
        )

    def list_attachments(self, day: str | None = None) -> list[dict[str, str]]:
        root = self.attachments
        if not root.exists():
            return []
        files: list[dict[str, str]] = []
        search_root = root / day if day else root
        if not search_root.exists():
            return []
        for path in sorted(search_root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            files.append(
                {
                    "name": path.name,
                    "relative": str(relative),
                    "day": relative.parts[0] if relative.parts else "",
                    "chat": relative.parts[1] if len(relative.parts) > 2 else "",
                }
            )
        return files

    def attachment_file(self, relative: str) -> Path:
        root = self.attachments.resolve()
        path = (self.attachments / relative).resolve()
        if path != root and root not in path.parents:
            raise ValueError("첨부파일 경로가 저장소 밖입니다.")
        if not path.is_file():
            raise FileNotFoundError(f"첨부파일이 없습니다: {relative}")
        return path

    def watched_chat_meta(self, chat_id: int) -> dict[str, object]:
        path = self.raw / str(chat_id) / "state.json"
        if not path.exists():
            return {"chat_id": chat_id, "title": None, "last_id": 0}
        state = ChatState.model_validate_json(path.read_text(encoding="utf-8"))
        return {
            "chat_id": state.chat_id,
            "title": state.title,
            "last_id": state.last_id,
            "last_collected_at": (
                state.last_collected_at.isoformat() if state.last_collected_at else None
            ),
        }

    def chat_titles_path(self) -> Path:
        return self.root / "chat_titles.json"

    def load_chat_titles(self) -> dict[str, str]:
        path = self.chat_titles_path()
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(key): str(value) for key, value in data.items() if value}

    def save_chat_title(self, chat_id: str | int, title: str | None) -> None:
        text = (title or "").strip()
        if not text or text == str(chat_id):
            return
        self.ensure()
        titles = self.load_chat_titles()
        titles[str(chat_id)] = text
        self.chat_titles_path().write_text(
            json.dumps(titles, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def save_chat_titles(self, items: Iterable[tuple[str | int, str]]) -> None:
        updated = False
        titles = self.load_chat_titles()
        for chat_id, title in items:
            text = (title or "").strip()
            if not text or text == str(chat_id):
                continue
            key = str(chat_id)
            if titles.get(key) != text:
                titles[key] = text
                updated = True
        if not updated:
            return
        self.ensure()
        self.chat_titles_path().write_text(
            json.dumps(titles, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def utcnow() -> datetime:
    from datetime import timezone

    return datetime.now(timezone.utc)
