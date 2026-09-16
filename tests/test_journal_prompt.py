from pathlib import Path

import pytest

from worklog_agent.journal import JOURNAL_SYSTEM_PROMPT
from worklog_agent.journal_prompt import (
    DEFAULT_SECTIONS,
    PromptError,
    build_system_prompt,
    load_system_prompt,
    reset_system_prompt,
    resolve_system_prompt,
    save_system_prompt,
)


def test_default_prompt_when_missing(tmp_path: Path) -> None:
    data = load_system_prompt(tmp_path)
    assert data["is_default"] is True
    assert data["sections"] == DEFAULT_SECTIONS
    assert data["system"] == JOURNAL_SYSTEM_PROMPT
    assert data["system"] == build_system_prompt(DEFAULT_SECTIONS)


def test_save_and_resolve_sections(tmp_path: Path) -> None:
    custom = ["요약", "커스텀 섹션"]
    saved = save_system_prompt(tmp_path, custom)
    assert saved["is_default"] is False
    assert saved["sections"] == custom
    loaded = load_system_prompt(tmp_path)
    assert loaded["sections"] == custom
    resolved = resolve_system_prompt(tmp_path, "2026-09-16")
    assert "# 업무 일지 (2026-09-16)" in resolved
    assert "## 요약" in resolved
    assert "## 커스텀 섹션" in resolved
    assert "## 수행 업무" not in resolved


def test_save_rejects_empty_and_duplicate(tmp_path: Path) -> None:
    with pytest.raises(PromptError, match="하나 이상"):
        save_system_prompt(tmp_path, [])
    with pytest.raises(PromptError, match="중복"):
        save_system_prompt(tmp_path, ["요약", "요약"])


def test_legacy_system_file_treated_as_default(tmp_path: Path) -> None:
    path = tmp_path / "journal_prompt.json"
    path.write_text('{"system": "옛 지시문 {date}"}\n', encoding="utf-8")
    data = load_system_prompt(tmp_path)
    assert data["is_default"] is True
    assert data["sections"] == DEFAULT_SECTIONS


def test_reset_prompt(tmp_path: Path) -> None:
    save_system_prompt(tmp_path, ["요약", "추가"])
    reset = reset_system_prompt(tmp_path)
    assert reset["is_default"] is True
    assert reset["sections"] == DEFAULT_SECTIONS
    assert reset["system"] == JOURNAL_SYSTEM_PROMPT
    assert not (tmp_path / "journal_prompt.json").exists()
