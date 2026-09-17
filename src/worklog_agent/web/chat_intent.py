from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

# Telegram BotFather username: 5–32 chars, letters/digits/underscore
_BOT_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")

# 생성 의도: 오늘/어제/그제 + 생성·만들어·돌려 등
_GENERATE_VERB = (
    r"(생성|만들어|만들어\s*줘|만들어줘|만들어주세요|작성|돌려|실행|뽑아|해\s*줘|해줘|해줘요|해 주세요)"
)
_DAY_TODAY = r"(오늘|금일)"
_DAY_YESTERDAY = r"(어제|어저께)"
_DAY_DAY_BEFORE = r"(그제|그저께)"

_SCHEDULE_WORD = r"(예약|스케줄)"
_SCHEDULE_CREATE = r"(추가해줘|추가|만들어줘|만들어|만들|등록|설정|잡아줘|잡아)"
_SCHEDULE_ENABLE = r"(켜줘|켜|활성화|활성|재개|on)"
_SCHEDULE_DISABLE = r"(꺼줘|꺼|끄줘|끄|비활성화|비활성|중지|멈춰|off)"
_SCHEDULE_DELETE = r"(삭제해줘|삭제|지워줘|지워|제거|없애줘|없애)"
_WATCH_ON = r"(켜줘|켜줘요|켜\s*주세요|켜주세요|켜|활성화|활성|추가해줘|추가|넣어줘|넣어|등록|on)"
_WATCH_OFF = r"(꺼줘|꺼줘요|끄줘|꺼\s*주세요|꺼주세요|꺼|끄|비활성화|비활성|삭제|빼줘|빼|제외|off|멈춰줘|멈춰)"


def local_today(tz_name: str = "Asia/Seoul") -> date:
    return datetime.now(ZoneInfo(tz_name)).date()


def resolve_relative_day(token: str, *, tz_name: str = "Asia/Seoul") -> str | None:
    today = local_today(tz_name)
    raw = token.strip()
    if re.fullmatch(_DAY_TODAY, raw):
        return today.isoformat()
    if re.fullmatch(_DAY_YESTERDAY, raw):
        return (today - timedelta(days=1)).isoformat()
    if re.fullmatch(_DAY_DAY_BEFORE, raw):
        return (today - timedelta(days=2)).isoformat()
    return None


def clean_room_query(raw: str) -> str:
    s = str(raw or "").strip()
    s = re.sub(r"^(의|에|을|를|이|가|은|는)\s*", "", s)
    s = re.sub(r"\s*(채팅\s*)?방\s*$", "", s)
    s = re.sub(r"\s*(을|를|은|는|이|가)\s*$", "", s)
    s = re.sub(r"\s+", " ", s).strip(" ·-_")
    return s


def parse_watch_intent(question: str) -> dict[str, Any] | None:
    """수집방 on/off. '수집' 키워드가 있어야 생성 의도(어제 꺼 …)와 충돌하지 않음."""
    q = str(question or "").strip()
    if not q or not re.search(r"수집", q):
        return None

    # 수집방 전부 꺼줘 / 모든 수집 대상 비활성화
    if re.search(
        rf"^(모든|전체)?\s*수집(\s*(방|대상))?\s*(을?\s*)?(전부|모두|다|전체)?\s*{_WATCH_OFF}\s*$",
        q,
        flags=re.IGNORECASE,
    ):
        return {"intent": "watch", "action": "off", "room_query": None, "all": True}

    # 수집방 금형 켜줘 / 수집 대상 금형방 꺼줘
    m = re.search(
        rf"^수집(\s*(방|대상))?\s+(.+?)\s*{_WATCH_ON}\s*$",
        q,
        flags=re.IGNORECASE,
    )
    if m:
        return {
            "intent": "watch",
            "action": "on",
            "room_query": clean_room_query(m.group(3)),
            "all": False,
        }
    m = re.search(
        rf"^수집(\s*(방|대상))?\s+(.+?)\s*{_WATCH_OFF}\s*$",
        q,
        flags=re.IGNORECASE,
    )
    if m:
        name = clean_room_query(m.group(3))
        if name in {"전부", "모두", "다", "전체", "모든"}:
            return {"intent": "watch", "action": "off", "room_query": None, "all": True}
        return {"intent": "watch", "action": "off", "room_query": name, "all": False}

    # 금형방 수집 켜줘 / 금형 수집방 꺼줘
    m = re.search(
        rf"^(.+?)\s*수집(\s*(방|대상))?\s*{_WATCH_ON}\s*$",
        q,
        flags=re.IGNORECASE,
    )
    if m:
        name = clean_room_query(m.group(1))
        if name:
            return {"intent": "watch", "action": "on", "room_query": name, "all": False}
    m = re.search(
        rf"^(.+?)\s*수집(\s*(방|대상))?\s*{_WATCH_OFF}\s*$",
        q,
        flags=re.IGNORECASE,
    )
    if m:
        name = clean_room_query(m.group(1))
        if name in {"전부", "모두", "다", "전체", "모든"}:
            return {"intent": "watch", "action": "off", "room_query": None, "all": True}
        if name:
            return {"intent": "watch", "action": "off", "room_query": name, "all": False}

    return None


def extract_schedule_time(text: str) -> str | None:
    q = str(text or "")
    m = re.search(r"(?<!\d)([01]?\d|2[0-3])\s*:\s*([0-5]\d)(?!\d)", q)
    if m:
        return f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"
    m = re.search(r"(오전|오후)?\s*([1-9]|1[0-2]|0?\d)\s*시\s*([0-5]?\d)?\s*분?", q)
    if m:
        meridiem = m.group(1) or ""
        hour = int(m.group(2))
        minute = int(m.group(3) or 0)
        if meridiem == "오후" and hour < 12:
            hour += 12
        elif meridiem == "오전" and hour == 12:
            hour = 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"
    return None


def extract_schedule_target(text: str) -> str:
    if re.search(_DAY_TODAY, text):
        return "today"
    return "yesterday"


def clean_schedule_query(raw: str) -> str:
    s = str(raw or "").strip()
    s = re.sub(rf"^{_SCHEDULE_WORD}\s*", "", s)
    s = re.sub(rf"\s*{_SCHEDULE_WORD}\s*$", "", s)
    s = re.sub(
        rf"\s*({_SCHEDULE_CREATE}|{_SCHEDULE_ENABLE}|{_SCHEDULE_DISABLE}|{_SCHEDULE_DELETE})\s*$",
        "",
        s,
    )
    s = re.sub(r"\s*(을|를|은|는|이|가|의)\s*$", "", s)
    return re.sub(r"\s+", " ", s).strip(" ·-_")


def parse_schedule_intent(question: str) -> dict[str, Any] | None:
    """예약 추가/활성/비활성/삭제. '예약|스케줄' 키워드 필요."""
    q = str(question or "").strip()
    if not q or not re.search(_SCHEDULE_WORD, q):
        return None

    if re.search(rf"{_SCHEDULE_WORD}\s*(목록|리스트|보여|알려)", q):
        return {"intent": "schedule", "action": "list", "name_query": None, "time": None, "target": None}

    if re.search(_SCHEDULE_DELETE, q):
        name = clean_schedule_query(
            re.sub(_SCHEDULE_DELETE, " ", re.sub(_SCHEDULE_WORD, " ", q))
        )
        name = re.sub(r"(오전|오후)?\s*\d{1,2}\s*:\s*\d{2}", " ", name)
        name = re.sub(r"(오전|오후)?\s*\d{1,2}\s*시(\s*\d{1,2}\s*분)?", " ", name)
        name = re.sub(rf"({_DAY_TODAY}|{_DAY_YESTERDAY}|일지|매일)", " ", name)
        name = re.sub(r"\s+", " ", name).strip(" ·-_")
        return {
            "intent": "schedule",
            "action": "delete",
            "name_query": name or None,
            "time": extract_schedule_time(q),
            "target": None,
        }

    if re.search(_SCHEDULE_DISABLE, q) and not re.search(_SCHEDULE_CREATE, q):
        name = clean_schedule_query(
            re.sub(_SCHEDULE_DISABLE, " ", re.sub(_SCHEDULE_WORD, " ", q))
        )
        name = re.sub(r"(오전|오후)?\s*\d{1,2}\s*:\s*\d{2}", " ", name)
        name = re.sub(r"(오전|오후)?\s*\d{1,2}\s*시(\s*\d{1,2}\s*분)?", " ", name)
        name = re.sub(rf"({_DAY_TODAY}|{_DAY_YESTERDAY}|일지|매일)", " ", name)
        name = re.sub(r"\s+", " ", name).strip(" ·-_")
        return {
            "intent": "schedule",
            "action": "disable",
            "name_query": name or None,
            "time": extract_schedule_time(q),
            "target": None,
        }

    if re.search(_SCHEDULE_ENABLE, q) and not re.search(_SCHEDULE_CREATE, q):
        name = clean_schedule_query(
            re.sub(_SCHEDULE_ENABLE, " ", re.sub(_SCHEDULE_WORD, " ", q))
        )
        name = re.sub(r"(오전|오후)?\s*\d{1,2}\s*:\s*\d{2}", " ", name)
        name = re.sub(r"(오전|오후)?\s*\d{1,2}\s*시(\s*\d{1,2}\s*분)?", " ", name)
        name = re.sub(rf"({_DAY_TODAY}|{_DAY_YESTERDAY}|일지|매일)", " ", name)
        name = re.sub(r"\s+", " ", name).strip(" ·-_")
        return {
            "intent": "schedule",
            "action": "enable",
            "name_query": name or None,
            "time": extract_schedule_time(q),
            "target": None,
        }

    if re.search(_SCHEDULE_CREATE, q) or re.search(
        rf"{_SCHEDULE_WORD}\s*(을?\s*)?(추가|만들|등록|설정)", q
    ):
        time_str = extract_schedule_time(q)
        target = extract_schedule_target(q)
        name = clean_schedule_query(
            re.sub(_SCHEDULE_CREATE, " ", re.sub(_SCHEDULE_WORD, " ", q))
        )
        # strip time / target words from name
        name = re.sub(r"(오전|오후)?\s*\d{1,2}\s*:\s*\d{2}", " ", name)
        name = re.sub(r"(오전|오후)?\s*\d{1,2}\s*시(\s*\d{1,2}\s*분)?", " ", name)
        name = re.sub(rf"({_DAY_TODAY}|{_DAY_YESTERDAY}|일지|매일)", " ", name)
        name = re.sub(r"\s+", " ", name).strip(" ·-_")
        return {
            "intent": "schedule",
            "action": "create",
            "name_query": name or None,
            "time": time_str,
            "target": target,
        }

    return None


def parse_chat_intent(question: str, *, tz_name: str = "Asia/Seoul") -> dict[str, Any]:
    """Rule-based intent for journal chat. Returns search, generate, watch, or schedule."""
    q = str(question or "").strip()
    if not q:
        return {"intent": "search", "dates": [], "label": None}

    watch = parse_watch_intent(q)
    if watch:
        return watch

    schedule = parse_schedule_intent(q)
    if schedule:
        return schedule

    # 「어제 일지 생성해줘」「오늘거 만들어줘」「그제 꺼 돌려줘」
    gen_patterns = [
        rf"^{_DAY_TODAY}\s*(거|꺼|일지|업무\s*일지)?\s*{_GENERATE_VERB}",
        rf"^{_DAY_YESTERDAY}\s*(거|꺼|일지|업무\s*일지)?\s*{_GENERATE_VERB}",
        rf"^{_DAY_DAY_BEFORE}\s*(거|꺼|일지|업무\s*일지)?\s*{_GENERATE_VERB}",
        rf"^{_GENERATE_VERB}\s*{_DAY_TODAY}\s*(거|꺼|일지)?",
        rf"^{_GENERATE_VERB}\s*{_DAY_YESTERDAY}\s*(거|꺼|일지)?",
        rf"^{_GENERATE_VERB}\s*{_DAY_DAY_BEFORE}\s*(거|꺼|일지)?",
        rf"^{_DAY_TODAY}\s*(의\s*)?(일지|업무\s*일지)\s*{_GENERATE_VERB}",
        rf"^{_DAY_YESTERDAY}\s*(의\s*)?(일지|업무\s*일지)\s*{_GENERATE_VERB}",
        rf"^{_DAY_DAY_BEFORE}\s*(의\s*)?(일지|업무\s*일지)\s*{_GENERATE_VERB}",
    ]
    for pattern in gen_patterns:
        if re.search(pattern, q, flags=re.IGNORECASE):
            label = "오늘"
            day = resolve_relative_day("오늘", tz_name=tz_name)
            if re.search(_DAY_YESTERDAY, q):
                label = "어제"
                day = resolve_relative_day("어제", tz_name=tz_name)
            elif re.search(_DAY_DAY_BEFORE, q):
                label = "그제"
                day = resolve_relative_day("그제", tz_name=tz_name)
            dates = [day] if day else []
            return {"intent": "generate", "dates": dates, "label": label}

    # ISO date + generate
    iso = re.search(r"(\d{4}-\d{2}-\d{2})", q)
    if iso and re.search(_GENERATE_VERB, q, flags=re.IGNORECASE):
        day = iso.group(1)
        try:
            date.fromisoformat(day)
        except ValueError:
            return {"intent": "search", "dates": [], "label": None}
        return {"intent": "generate", "dates": [day], "label": day}

    return {"intent": "search", "dates": [], "label": None}


def normalize_bot_username(bot_username: str | None) -> str | None:
    raw = str(bot_username or "").strip()
    if not raw:
        return None
    if raw.startswith("https://t.me/") or raw.startswith("http://t.me/"):
        raw = raw.rstrip("/").rsplit("/", 1)[-1]
    elif raw.startswith("t.me/"):
        raw = raw.rstrip("/").rsplit("/", 1)[-1]
    name = raw.lstrip("@").split("?", 1)[0].strip()
    if not _BOT_USERNAME_RE.fullmatch(name):
        return None
    return name


def telegram_bot_url(bot_username: str | None, *, start: str | None = None) -> str | None:
    name = normalize_bot_username(bot_username)
    if not name:
        return None
    base = f"https://t.me/{name}"
    token = str(start or "").strip()
    if token:
        return f"{base}?start={token}"
    return base
