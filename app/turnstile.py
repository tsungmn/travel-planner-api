"""Cloudflare Turnstile (봇 방지) 서버 검증.

TURNSTILE_SECRET_KEY 가 비어 있으면 검사하지 않는다. (키를 넣기 전에도 서비스가 그대로 동작하게)
검증 서버에 닿지 못하면 통과시키지 않는다. (봇이 그 틈을 노리지 못하게. 이때 가입은 잠시 막힌다)
"""
import logging

import httpx
from fastapi import HTTPException

from .config import settings

log = logging.getLogger("turnstile")

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TIMEOUT_S = 5.0


async def _siteverify(secret: str, token: str, ip: str | None) -> bool:
    data = {"secret": secret, "response": token}
    if ip and ip != "unknown":
        data["remoteip"] = ip
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            r = await client.post(VERIFY_URL, data=data)
        r.raise_for_status()
        return bool(r.json().get("success"))
    except Exception:
        log.exception("Turnstile 검증 요청 실패")
        return False


async def require(token: str | None, ip: str | None) -> None:
    """검사가 켜져 있는데 토큰이 없거나 틀리면 400 captcha_failed."""
    secret = settings.turnstile_secret_key
    if not secret:
        return
    if not token or not await _siteverify(secret, token, ip):
        raise HTTPException(400, "captcha_failed")
