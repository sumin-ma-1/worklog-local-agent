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


class StorageConfig(BaseModel):
    root: Path = Path("data")


class CollectConfig(BaseModel):
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

    @property
    def data_root(self) -> Path:
        return Path(self.storage.root).expanduser().resolve()


def load_config(path: Path | None = None) -> AppConfig:
    config_path = path or Path("config.yaml")
    data: dict = {}
    if config_path.exists():
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"설정 파일이 객체가 아닙니다: {config_path}")
        data = loaded
    else:
        logger.warning("설정 파일이 없어 기본값을 사용합니다: %s", config_path)

    env = EnvSettings()
    config = AppConfig.model_validate({**data, "env": env})
    if env.ollama_host:
        config.journal.ollama.host = env.ollama_host
    if env.ollama_model:
        config.journal.ollama.model = env.ollama_model
    return config
