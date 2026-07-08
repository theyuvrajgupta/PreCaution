"""Runtime configuration, loaded from environment / .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    precaution_model: str = "claude-sonnet-5"


@lru_cache
def get_settings() -> Settings:
    return Settings()
