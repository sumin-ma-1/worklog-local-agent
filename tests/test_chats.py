from pathlib import Path

import pytest

from worklog_agent.config import add_chat_ref, load_config, remove_chat_ref


def _write_config(path: Path, root: Path) -> Path:
    path.write_text(
        "\n".join(
            [
                "timezone: Asia/Seoul",
                "telegram:",
                "  session_name: worklog",
                "  chats: []",
                "storage:",
                f"  root: {root}",
                "collect:",
                "  lookback_days: 7",
                "  skip_service_messages: true",
                "journal:",
                "  language: ko",
                "  temperature: 0.2",
                "  ollama:",
                "    host: http://127.0.0.1:11434",
                "    model: gemma4:e4b",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_add_and_remove_chat_ids(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path / "config.yaml", tmp_path / "data")
    config = load_config(config_path)
    config = add_chat_ref(config, "-1001234567890")
    assert config.telegram.chats == [-1001234567890]
    reloaded = load_config(config_path)
    assert reloaded.telegram.chats == [-1001234567890]
    config = remove_chat_ref(config, -1001234567890)
    assert config.telegram.chats == []
    assert load_config(config_path).telegram.chats == []


def test_add_duplicate_chat_rejected(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path / "config.yaml", tmp_path / "data")
    config = add_chat_ref(load_config(config_path), 123)
    with pytest.raises(ValueError, match="이미 등록된"):
        add_chat_ref(config, "123")
