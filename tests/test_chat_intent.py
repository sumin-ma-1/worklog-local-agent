from datetime import date

from worklog_agent.web.chat_intent import parse_chat_intent, resolve_relative_day, telegram_bot_url


def test_telegram_bot_url() -> None:
    assert telegram_bot_url(None) is None
    assert telegram_bot_url("") is None
    assert telegram_bot_url("WorklogBot") == "https://t.me/WorklogBot"
    assert telegram_bot_url("@WorklogBot") == "https://t.me/WorklogBot"
    assert telegram_bot_url("https://t.me/WorklogBot") == "https://t.me/WorklogBot"
    assert telegram_bot_url("WorklogBot", start="tok") == "https://t.me/WorklogBot?start=tok"
    # 표시 이름·공백은 유효한 BotFather username 이 아님
    assert telegram_bot_url("<KETI> Worklog") is None
    assert telegram_bot_url("ab") is None


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


def test_parse_chat_intent_watch() -> None:
    off = parse_chat_intent("금형방 수집 꺼줘")
    assert off["intent"] == "watch"
    assert off["action"] == "off"
    assert off["room_query"] == "금형"
    assert off["all"] is False

    on = parse_chat_intent("수집방 금형 켜줘")
    assert on["intent"] == "watch"
    assert on["action"] == "on"
    assert on["room_query"] == "금형"

    all_off = parse_chat_intent("수집방 전부 꺼줘")
    assert all_off["intent"] == "watch"
    assert all_off["action"] == "off"
    assert all_off["all"] is True

    # 생성 의도(어제 꺼 …)와 충돌하지 않음
    gen = parse_chat_intent("그제 꺼 돌려줘", tz_name="Asia/Seoul")
    assert gen["intent"] == "generate"


def test_parse_chat_intent_schedule() -> None:
    create = parse_chat_intent("매일 9시 어제 일지 예약 추가해줘")
    assert create["intent"] == "schedule"
    assert create["action"] == "create"
    assert create["time"] == "09:00"
    assert create["target"] == "yesterday"

    disable = parse_chat_intent("아침 예약 꺼줘")
    assert disable["intent"] == "schedule"
    assert disable["action"] == "disable"

    delete = parse_chat_intent("어제 09:00 예약 삭제해줘")
    assert delete["intent"] == "schedule"
    assert delete["action"] == "delete"
    assert delete["time"] == "09:00"

    listing = parse_chat_intent("예약 목록 보여줘")
    assert listing["intent"] == "schedule"
    assert listing["action"] == "list"


def test_resolve_relative_day(monkeypatch) -> None:
    monkeypatch.setattr(
        "worklog_agent.web.chat_intent.local_today",
        lambda tz_name="Asia/Seoul": date(2026, 9, 17),
    )
    assert resolve_relative_day("오늘") == "2026-09-17"
    assert resolve_relative_day("어제") == "2026-09-16"
    assert resolve_relative_day("그제") == "2026-09-15"
