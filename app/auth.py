"""Session-based single-user authentication with rate limiting."""

from __future__ import annotations

import hmac
import secrets
import time
from collections import defaultdict, deque
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import Settings, get_settings


class LoginRateLimiter:
    def __init__(self) -> None:
        self._attempts: dict[str, deque[float]] = defaultdict(deque)

    def is_allowed(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        bucket = self._attempts[key]
        while bucket and now - bucket[0] > window_seconds:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True

    def reset(self, key: str) -> None:
        self._attempts.pop(key, None)


rate_limiter = LoginRateLimiter()


def get_serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        settings.session_secret,
        salt="whisper-session",
    )


def create_session_token(settings: Settings) -> str:
    serializer = get_serializer(settings)
    return serializer.dumps({"authenticated": True, "nonce": secrets.token_hex(8)})


def verify_session_token(token: str, settings: Settings) -> bool:
    serializer = get_serializer(settings)
    try:
        data = serializer.loads(token, max_age=settings.session_max_age_seconds)
        return bool(data.get("authenticated"))
    except (BadSignature, SignatureExpired):
        return False


def set_session_cookie(response: Response, settings: Settings) -> None:
    token = create_session_token(settings)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        httponly=True,
        secure=settings.session_https_only,
        samesite="lax",
        max_age=settings.session_max_age_seconds,
        path="/",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        httponly=True,
        secure=settings.session_https_only,
        samesite="lax",
    )


def passwords_match(provided: str, expected: str) -> bool:
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def is_authenticated(request: Request, settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        return False
    return verify_session_token(token, settings)


async def require_auth(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    if not is_authenticated(request, settings):
        accept = request.headers.get("accept", "")
        if "text/html" in accept and not request.url.path.startswith("/api/"):
            raise HTTPException(
                status_code=status.HTTP_303_SEE_OTHER,
                headers={"Location": "/login"},
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"
