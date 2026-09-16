from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent
from typing import Any

_PROMPT_FILE = "journal_prompt.json"
_MAX_SECTIONS = 20
_MAX_SECTION_CHARS = 40

DEFAULT_SECTIONS: list[str] = [
    "요약",
    "수행 업무",
    "논의 / 결정 사항",
    "요청 / 협업",
    "이슈 및 리스크",
    "산출물 / 첨부",
    "다음 액션",
]

_PROMPT_INTRO = dedent(
    """
    당신은 업무 채팅 기록을 바탕으로 실무자가 바로 쓸 수 있는 일지를 작성하는 비서입니다.
    추측하지 말고 주어진 메시지와 첨부 목록에만 근거하세요.
    시간 순서를 유지하고, 사람 이름·파일명·결정 사항은 원문 표현을 보존하세요.
    출력은 반드시 아래 마크다운 형식을 지키세요.

    # 업무 일지 ({date})
    """
).strip()

_PROMPT_OUTRO = '해당 항목에 내용이 없으면 "없음"이라고 적습니다.'


class PromptError(ValueError):
    pass


def normalize_section_name(raw: str) -> str:
    text = str(raw or "").strip()
    if text.startswith("##"):
        text = text[2:].strip()
    return text


def build_system_prompt(sections: list[str] | None = None) -> str:
    names = list(sections) if sections is not None else list(DEFAULT_SECTIONS)
    lines = [_PROMPT_INTRO, ""]
    for name in names:
        lines.append(f"## {name}")
    lines.extend(["", _PROMPT_OUTRO])
    return "\n".join(lines)


def default_system_prompt() -> str:
    return build_system_prompt(DEFAULT_SECTIONS)


def default_sections() -> list[str]:
    return list(DEFAULT_SECTIONS)


def prompt_path(user_root: Path) -> Path:
    return Path(user_root) / _PROMPT_FILE


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return raw if isinstance(raw, dict) else None


def _normalize_sections(raw: Any) -> list[str] | None:
    if not isinstance(raw, list):
        return None
    names: list[str] = []
    for item in raw:
        name = normalize_section_name(str(item) if item is not None else "")
        if not name:
            continue
        names.append(name)
    return names or None


def validate_sections(sections: list[str]) -> list[str]:
    if not sections:
        raise PromptError("섹션을 하나 이상 두세요.")
    if len(sections) > _MAX_SECTIONS:
        raise PromptError(f"섹션은 {_MAX_SECTIONS}개까지 가능합니다.")
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in sections:
        name = normalize_section_name(raw)
        if not name:
            raise PromptError("빈 섹션 이름은 사용할 수 없습니다.")
        if len(name) > _MAX_SECTION_CHARS:
            raise PromptError(f"섹션 이름은 {_MAX_SECTION_CHARS}자 이하여야 합니다.")
        key = name.casefold()
        if key in seen:
            raise PromptError(f"중복된 섹션입니다: {name}")
        seen.add(key)
        cleaned.append(name)
    return cleaned


def _payload(sections: list[str], *, is_default: bool, updated_at: str | None) -> dict[str, Any]:
    return {
        "sections": sections,
        "is_default": is_default,
        "updated_at": updated_at,
        "system": build_system_prompt(sections),
    }


def load_system_prompt(user_root: Path) -> dict[str, Any]:
    stored = _read_json(prompt_path(user_root))
    defaults = default_sections()
    if not stored:
        return _payload(defaults, is_default=True, updated_at=None)
    sections = _normalize_sections(stored.get("sections"))
    if not sections:
        # Legacy {system: "..."} or empty → treat as default.
        return _payload(defaults, is_default=True, updated_at=None)
    is_default = sections == defaults
    return _payload(sections, is_default=is_default, updated_at=stored.get("updated_at"))


def save_system_prompt(user_root: Path, sections: list[str]) -> dict[str, Any]:
    cleaned = validate_sections(sections)
    payload = {
        "sections": cleaned,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    path = prompt_path(user_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return _payload(cleaned, is_default=cleaned == default_sections(), updated_at=payload["updated_at"])


def reset_system_prompt(user_root: Path) -> dict[str, Any]:
    path = prompt_path(user_root)
    if path.is_file():
        path.unlink()
    return _payload(default_sections(), is_default=True, updated_at=None)


def resolve_system_prompt(user_root: Path, day: str) -> str:
    text = load_system_prompt(user_root)["system"]
    return text.replace("{date}", day)
