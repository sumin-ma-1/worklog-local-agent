from worklog_agent.journal import render_draft
from worklog_agent.models import DailyBundle, DailyChat, MediaRef, MessageRecord
from datetime import datetime, timezone


def test_render_draft_includes_timeline_and_attachments() -> None:
    bundle = DailyBundle(
        date="2026-09-03",
        timezone="Asia/Seoul",
        chats=[
            DailyChat(
                chat_id=-1001,
                title="팀 업무방",
                message_count=1,
                participants=["김수민"],
                messages=[
                    MessageRecord(
                        id=1,
                        chat_id=-1001,
                        chat_title="팀 업무방",
                        date=datetime(2026, 9, 3, 9, 0, tzinfo=timezone.utc),
                        sender_name="김수민",
                        text="배포 완료",
                        media=MediaRef(
                            type="document",
                            file_name="notes.md",
                            local_path="/tmp/notes.md",
                        ),
                    )
                ],
                attachments=[
                    MediaRef(type="document", file_name="notes.md", local_path="/tmp/notes.md")
                ],
            )
        ],
        totals={"messages": 1, "attachments": 1, "chats": 1},
    )
    markdown = render_draft(bundle)
    assert "# 업무 일지 (2026-09-03)" in markdown
    assert "김수민: 배포 완료" in markdown
    assert "notes.md" in markdown
