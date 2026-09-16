from worklog_agent.web.file_icons import file_icon_key, file_icon_src


def test_file_icon_key_known_and_fallback() -> None:
    assert file_icon_key("report.pdf") == "pdf"
    assert file_icon_key("shot.JPEG") == "jpg"
    assert file_icon_key("notes.markdown") == "md"
    assert file_icon_key("archive.unknown") == "etc"
    assert file_icon_key("README") == "etc"
    assert file_icon_src("a.docx") == "/static/file-icons/docx.svg"
