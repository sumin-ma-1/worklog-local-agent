from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from worklog_agent.web.app import create_app


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


def test_dashboard_add_and_delete_chat(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path / "config.yaml", tmp_path / "data")
    client = TestClient(create_app(config_path))
    home = client.get("/")
    assert home.status_code == 200
    assert "업무방" in home.text

    added = client.post("/api/chats", json={"id": "-100111"})
    assert added.status_code == 200
    assert added.json()["chats"][0]["id"] == -100111

    listed = client.get("/api/chats")
    assert [row["id"] for row in listed.json()["chats"]] == [-100111]

    removed = client.post("/api/chats/delete", json={"id": -100111})
    assert removed.status_code == 200
    assert removed.json()["chats"] == []


def test_dialogs_mark_watched(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path / "config.yaml", tmp_path / "data")
    app = create_app(config_path)
    client = TestClient(app)
    client.post("/api/chats", json={"id": -1001})
    fake = [{"id": -1001, "title": "팀 업무방", "type": "supergroup"}]
    with patch("worklog_agent.collect.load_dialogs", new=AsyncMock(return_value=fake)):
        data = client.get("/api/dialogs").json()
    assert data["dialogs"][0]["watched"] is True
    assert data["dialogs"][0]["title"] == "팀 업무방"
