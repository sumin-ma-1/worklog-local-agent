from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from worklog_agent.storage import Storage


def journal_meta_path(storage: Storage, day: str):
    return storage.journals / f"{day}.meta.json"


def read_journal_meta(storage: Storage, day: str) -> dict[str, Any] | None:
    path = journal_meta_path(storage, day)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def write_journal_meta(
    storage: Storage,
    day: str,
    *,
    source_fingerprint: str,
    model: str | None = None,
) -> None:
    storage.journals.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": day,
        "source_fingerprint": source_fingerprint,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if model:
        payload["model"] = model
    journal_meta_path(storage, day).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def delete_journal_meta(storage: Storage, day: str) -> bool:
    path = journal_meta_path(storage, day)
    if not path.is_file():
        return False
    path.unlink()
    return True
