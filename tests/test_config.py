from pathlib import Path

from worklog_agent.config import load_config


def test_load_example_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    example = Path(__file__).resolve().parents[1] / "config.example.yaml"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    config = load_config(config_path)
    assert config.timezone == "Asia/Seoul"
    assert config.telegram.chats == ["팀 업무방"]
    assert config.collect.lookback_days == 7
    assert config.journal.ollama.host == "http://127.0.0.1:11434"
    assert config.journal.ollama.model == "gemma4:e4b"
