from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class EnvSettings(BaseSettings):
    telegram_api_id: int | None = None
    telegram_api_hash: str | None = None
    telegram_phone: str | None = None
    telegram_bot_token: str | None = None
    ollama_host: str | None = None
    ollama_model: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator(
        "telegram_api_id",
        "telegram_api_hash",
        "telegram_phone",
        "telegram_bot_token",
        "ollama_host",
        "ollama_model",
        mode="before",
    )
    @classmethod
    def empty_to_none(cls, value: object) -> object:
        if value == "":
            return None
        return value


class TelegramConfig(BaseModel):
    session_name: str = "worklog"
    chats: list[str | int] = Field(default_factory=list)
    # 대시보드 챗 헤더 바로가기용 BotFather username (없으면 버튼 숨김)
    bot_username: str | None = None


class StorageConfig(BaseModel):
    root: Path = Path("data")


class CollectConfig(BaseModel):
    # 더 이상 수집 범위에 쓰이지 않음. 실행 시 선택한 날짜만 수집합니다.
    lookback_days: int = 7
    skip_service_messages: bool = True


class OllamaConfig(BaseModel):
    host: str = "http://127.0.0.1:11434"
    model: str = "gemma4:e4b"
    timeout_seconds: float = 300
    num_ctx: int = 16384


class JournalConfig(BaseModel):
    language: str = "ko"
    temperature: float = 0.2
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)


class AppConfig(BaseModel):
    timezone: str = "Asia/Seoul"
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    collect: CollectConfig = Field(default_factory=CollectConfig)
    journal: JournalConfig = Field(default_factory=JournalConfig)
    env: EnvSettings = Field(default_factory=EnvSettings)
    config_path: Path = Path("config.yaml")
    env_path: Path = Path(".env")

    @property
    def data_root(self) -> Path:
        return Path(self.storage.root).expanduser().resolve()


def resolve_env_path(config_path: Path) -> Path:
    return (Path(config_path).expanduser().resolve().parent / ".env")


def load_config(path: Path | None = None) -> AppConfig:
    config_path = Path(path or "config.yaml").expanduser()
    if not config_path.is_absolute():
        config_path = config_path.resolve()
    env_path = resolve_env_path(config_path)
    data: dict = {}
    if config_path.exists():
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"설정 파일이 객체가 아닙니다: {config_path}")
        data = loaded
    else:
        logger.warning("설정 파일이 없어 기본값을 사용합니다: %s", config_path)

    env = EnvSettings(_env_file=env_path if env_path.exists() else None)
    config = AppConfig.model_validate(
        {**data, "env": env, "config_path": config_path, "env_path": env_path}
    )
    if env.ollama_host:
        config.journal.ollama.host = env.ollama_host
    if env.ollama_model:
        config.journal.ollama.model = env.ollama_model
    return config


_ENV_KEYS = (
    "TELEGRAM_API_ID",
    "TELEGRAM_API_HASH",
    "TELEGRAM_PHONE",
    "TELEGRAM_BOT_TOKEN",
    "OLLAMA_HOST",
    "OLLAMA_MODEL",
)


def save_env_values(env_path: Path, updates: dict[str, str | int | None]) -> Path:
    """Update selected keys in .env while preserving unrelated lines when possible."""
    env_path = Path(env_path)
    existing_lines = (
        env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    )
    normalized: dict[str, str] = {}
    for key, value in updates.items():
        if value is None:
            continue
        text = str(value).strip()
        if text:
            normalized[key] = text

    seen: set[str] = set()
    rewritten: list[str] = []
    for line in existing_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.partition("=")[0].strip()
            if key in normalized:
                rewritten.append(f"{key}={normalized[key]}")
                seen.add(key)
                continue
        rewritten.append(line)

    for key in _ENV_KEYS:
        if key in normalized and key not in seen:
            rewritten.append(f"{key}={normalized[key]}")

    if not existing_lines and not rewritten:
        rewritten = [
            "# https://my.telegram.org → API development tools",
            "TELEGRAM_API_ID=",
            "TELEGRAM_API_HASH=",
            "TELEGRAM_PHONE=",
        ]

    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(rewritten).rstrip() + "\n", encoding="utf-8")
    return env_path


def save_telegram_credentials(
    config: AppConfig,
    *,
    api_id: str | int | None = None,
    api_hash: str | None = None,
    phone: str | None = None,
) -> AppConfig:
    updates: dict[str, str | int | None] = {}
    if api_id is not None and str(api_id).strip():
        text = str(api_id).strip()
        if not text.isdigit():
            raise ValueError("TELEGRAM_API_ID 는 숫자여야 합니다.")
        updates["TELEGRAM_API_ID"] = text
    if api_hash is not None and str(api_hash).strip():
        updates["TELEGRAM_API_HASH"] = str(api_hash).strip()
    if phone is not None and str(phone).strip():
        updates["TELEGRAM_PHONE"] = str(phone).strip()
    if not updates:
        raise ValueError("저장할 값이 없습니다.")
    save_env_values(config.env_path, updates)
    return load_config(config.config_path)


def telegram_credential_summary(config: AppConfig) -> dict[str, object]:
    env = config.env
    api_hash = env.telegram_api_hash or ""
    phone = env.telegram_phone or ""
    return {
        "has_api_id": bool(env.telegram_api_id),
        "has_api_hash": bool(api_hash),
        "has_phone": bool(phone),
        "api_id": env.telegram_api_id,
        "api_hash_masked": (
            f"{'*' * max(0, len(api_hash) - 4)}{api_hash[-4:]}" if api_hash else ""
        ),
        "phone": phone,
        "env_path": str(config.env_path),
        "ready": bool(env.telegram_api_id and api_hash),
    }


def normalize_chat_ref(value: str | int) -> int | str:
    text = str(value).strip()
    if not text:
        raise ValueError("채팅방 ID가 비어 있습니다.")
    if text.lstrip("-").isdigit():
        return int(text)
    return text


def chat_ref_key(value: str | int) -> str:
    return str(normalize_chat_ref(value))


def save_config(config: AppConfig) -> None:
    payload = {
        "timezone": config.timezone,
        "telegram": {
            "session_name": config.telegram.session_name,
            "chats": [normalize_chat_ref(item) for item in config.telegram.chats],
        },
        "storage": {"root": str(config.storage.root)},
        "collect": {
            "lookback_days": config.collect.lookback_days,
            "skip_service_messages": config.collect.skip_service_messages,
        },
        "journal": {
            "language": config.journal.language,
            "temperature": config.journal.temperature,
            "ollama": {
                "host": config.journal.ollama.host,
                "model": config.journal.ollama.model,
                "timeout_seconds": config.journal.ollama.timeout_seconds,
                "num_ctx": config.journal.ollama.num_ctx,
            },
        },
    }
    path = Path(config.config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )


def add_chat_ref(config: AppConfig, value: str | int) -> AppConfig:
    ref = normalize_chat_ref(value)
    keys = {chat_ref_key(item) for item in config.telegram.chats}
    if chat_ref_key(ref) in keys:
        raise ValueError(f"이미 등록된 채팅방입니다: {ref}")
    config.telegram.chats.append(ref)
    save_config(config)
    return load_config(config.config_path)


def remove_chat_ref(config: AppConfig, value: str | int) -> AppConfig:
    key = chat_ref_key(value)
    remaining = [item for item in config.telegram.chats if chat_ref_key(item) != key]
    if len(remaining) == len(config.telegram.chats):
        raise ValueError(f"등록되지 않은 채팅방입니다: {value}")
    config.telegram.chats = remaining
    save_config(config)
    return load_config(config.config_path)
