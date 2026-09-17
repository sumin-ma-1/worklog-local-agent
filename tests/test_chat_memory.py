from pathlib import Path

from worklog_agent.web.journal_ask import append_chat_memory, load_chat_memory, save_chat_memory


def test_chat_memory_roundtrip(tmp_path: Path) -> None:
    root = tmp_path / "user"
    root.mkdir()
    append_chat_memory(root, "안녕", "안녕하세요")
    append_chat_memory(root, "어제 뭐 했지?", "배포 작업이 있었습니다.")
    turns = load_chat_memory(root)
    assert len(turns) == 4
    assert turns[0]["role"] == "user"
    assert turns[-1]["content"].startswith("배포")


def test_chat_memory_trims_old_turns(tmp_path: Path) -> None:
    root = tmp_path / "user"
    root.mkdir()
    for i in range(10):
        append_chat_memory(root, f"q{i}", f"a{i}")
    turns = load_chat_memory(root)
    assert len(turns) == 8
    assert turns[0]["content"] == "q6"
