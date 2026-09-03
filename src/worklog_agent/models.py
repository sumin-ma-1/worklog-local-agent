from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class MediaRef(BaseModel):
    type: str
    file_name: str | None = None
    mime_type: str | None = None
    size: int | None = None
    archived: bool = False
    local_path: str | None = None


class MessageRecord(BaseModel):
    id: int
    chat_id: int
    chat_title: str
    date: datetime
    sender_id: int | None = None
    sender_name: str | None = None
    text: str = ""
    reply_to_msg_id: int | None = None
    media: MediaRef | None = None

    def has_downloadable_media(self) -> bool:
        if self.media is None:
            return False
        return self.media.type in {
            "photo",
            "document",
            "video",
            "audio",
            "voice",
            "gif",
        }


class ChatState(BaseModel):
    chat_id: int
    title: str
    last_id: int = 0
    last_collected_at: datetime | None = None


class DailyChat(BaseModel):
    chat_id: int
    title: str
    message_count: int = 0
    participants: list[str] = Field(default_factory=list)
    messages: list[MessageRecord] = Field(default_factory=list)
    attachments: list[MediaRef] = Field(default_factory=list)


class DailyBundle(BaseModel):
    date: str
    timezone: str
    chats: list[DailyChat] = Field(default_factory=list)
    totals: dict[str, int] = Field(default_factory=dict)

    def to_prompt_payload(self) -> dict[str, Any]:
        chats = []
        for chat in self.chats:
            chats.append(
                {
                    "title": chat.title,
                    "message_count": chat.message_count,
                    "participants": chat.participants,
                    "messages": [
                        {
                            "time": msg.date.isoformat(),
                            "sender": msg.sender_name or str(msg.sender_id),
                            "text": msg.text,
                            "attachment": (
                                None
                                if msg.media is None
                                else {
                                    "type": msg.media.type,
                                    "file_name": msg.media.file_name,
                                    "local_path": msg.media.local_path,
                                }
                            ),
                        }
                        for msg in chat.messages
                    ],
                    "attachments": [
                        media.model_dump(mode="json") for media in chat.attachments
                    ],
                }
            )
        return {
            "date": self.date,
            "timezone": self.timezone,
            "totals": self.totals,
            "chats": chats,
        }
