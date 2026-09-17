from datetime import date

from worklog_agent.web.chat_intent import parse_chat_intent, resolve_relative_day, telegram_bot_url


def test_telegram_bot_url() -> None:
    assert telegram_bot_url(None) is None
    assert telegram_bot_url("") is None
    assert telegram_bot_url("WorklogBot") == "https://t.me/WorklogBot"
    assert telegram_bot_url("@WorklogBot") == "https://t.me/WorklogBot"
    assert telegram_bot_url("https://t.me/WorklogBot") == "https://t.me/WorklogBot"
    assert telegram_bot_url("WorklogBot", start="tok") == "https://t.me/WorklogBot?start=tok"


def test_parse_chat_intent_generate(monkeypatch) -> None:
    monkeypatch.setattr(
        "worklog_agent.web.chat_intent.local_today",
        lambda tz_name="Asia/Seoul": date(2026, 9, 17),
    )

    intent = parse_chat_intent("어제 일지 생성해줘", tz_name="Asia/Seoul")
    assert intent["intent"] == "generate"
    assert intent["dates"] == ["2026-09-16"]
    assert intent["label"] == "어제"

    today = parse_chat_intent("오늘거 만들어줘", tz_name="Asia/Seoul")
    assert today["intent"] == "generate"
    assert today["dates"] == ["2026-09-17"]

    day_before = parse_chat_intent("그제 꺼 돌려줘", tz_name="Asia/Seoul")
    assert day_before["intent"] == "generate"
    assert day_before["dates"] == ["2026-09-15"]

    search = parse_chat_intent("금형 과제 하던 날이 언제였지?", tz_name="Asia/Seoul")
    assert search["intent"] == "search"

    iso = parse_chat_intent("2026-09-10 일지 생성해줘", tz_name="Asia/Seoul")
    assert iso["intent"] == "generate"
    assert iso["dates"] == ["2026-09-10"]


def test_resolve_relative_day(monkeypatch) -> None:
    monkeypatch.setattr(
        "worklog_agent.web.chat_intent.local_today",
        lambda tz_name="Asia/Seoul": date(2026, 9, 17),
    )
    assert resolve_relative_day("오늘") == "2026-09-17"
    assert resolve_relative_day("어제") == "2026-09-16"
    assert resolve_relative_day("그제") == "2026-09-15"
