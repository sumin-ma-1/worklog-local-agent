from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

# 생성 의도: 오늘/어제/그제 + 생성·만들어·돌려 등
_GENERATE_VERB = (
    r"(생성|만들어|만들어\s*줘|만들어줘|만들어주세요|작성|돌려|실행|뽑아|해\s*줘|해줘|해줘요|해 주세요)"
)
_DAY_TODAY = r"(오늘|금일)"
_DAY_YESTERDAY = r"(어제|어저께)"
_DAY_DAY_BEFORE = r"(그제|그저께)"


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


def parse_chat_intent(question: str, *, tz_name: str = "Asia/Seoul") -> dict[str, Any]:
    """Rule-based intent for journal chat. Returns search or generate."""
    q = str(question or "").strip()
    if not q:
        return {"intent": "search", "dates": [], "label": None}

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


def telegram_bot_url(bot_username: str | None, *, start: str | None = None) -> str | None:
    raw = str(bot_username or "").strip()
    if not raw:
        return None
    if raw.startswith("https://t.me/") or raw.startswith("http://t.me/"):
        base = raw.rstrip("/")
    elif raw.startswith("t.me/"):
        base = f"https://{raw}".rstrip("/")
    else:
        name = raw.lstrip("@")
        if not name:
            return None
        base = f"https://t.me/{name}"
    token = str(start or "").strip()
    if token:
        return f"{base}?start={token}"
    return base
