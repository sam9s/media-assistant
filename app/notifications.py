from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

import httpx

from app.config import settings

logger = logging.getLogger("uvicorn.error")


def notifications_enabled() -> bool:
    return bool(
        settings.RAVEN_NOTIFICATIONS_ENABLED
        and settings.RAVEN_TELEGRAM_BOT_TOKEN
        and settings.RAVEN_TELEGRAM_CHAT_ID
    )


async def send_telegram_message(text: str) -> bool:
    if not notifications_enabled():
        return False

    url = f"https://api.telegram.org/bot{settings.RAVEN_TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": settings.RAVEN_TELEGRAM_CHAT_ID, "text": text}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload)
        resp.raise_for_status()
        return True
    except Exception as exc:
        logger.warning("Telegram notification failed: %s", exc)
        return False


async def wait_for_condition(
    predicate: Callable[[], Awaitable[bool]],
    *,
    attempts: int = 12,
    interval_seconds: int = 5,
) -> bool:
    for _ in range(attempts):
        try:
            if await predicate():
                return True
        except Exception:
            pass
        await asyncio.sleep(interval_seconds)
    return False
