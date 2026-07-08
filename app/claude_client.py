"""Thin wrapper around the Anthropic SDK so callers don't construct it directly."""

from functools import lru_cache

import anthropic

from app.config import get_settings


@lru_cache
def get_client() -> anthropic.Anthropic:
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and fill in a real key."
        )
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)
