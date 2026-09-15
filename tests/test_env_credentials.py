from pathlib import Path

import pytest

from worklog_agent.config import load_config, save_telegram_credentials, telegram_credential_summary


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


def test_save_telegram_credentials_to_env(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path / "config.yaml", tmp_path / "data")
    config = load_config(config_path)
    updated = save_telegram_credentials(
        config,
        api_id="12345",
        api_hash="abcdef123456",
        phone="+821011122233",
    )
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "TELEGRAM_API_ID=12345" in env_text
    assert "TELEGRAM_API_HASH=abcdef123456" in env_text
    assert "TELEGRAM_PHONE=+821011122233" in env_text
    assert updated.env.telegram_api_id == 12345
    summary = telegram_credential_summary(updated)
    assert summary["ready"] is True
    assert summary["api_hash_masked"].endswith("3456")


def test_save_rejects_non_numeric_api_id(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path / "config.yaml", tmp_path / "data")
    config = load_config(config_path)
    with pytest.raises(ValueError, match="숫자"):
        save_telegram_credentials(config, api_id="abc")
