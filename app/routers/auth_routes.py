"""Login / logout API and helpers."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import (
    clear_session_cookie,
    client_ip,
    is_authenticated,
    passwords_match,
    rate_limiter,
    set_session_cookie,
)
from app.config import Settings, get_settings
from app.schemas import MessageResponse

router = APIRouter(tags=["auth"])


def get_templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates


@router.post("/api/login", response_model=MessageResponse)
async def api_login(
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
) -> MessageResponse:
    ip = client_ip(request)
    if not rate_limiter.is_allowed(
        f"login:{ip}",
        settings.login_rate_limit,
        settings.login_rate_window_seconds,
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Please wait and try again.",
        )

    content_type = request.headers.get("content-type", "")
    password = ""
    if "application/json" in content_type:
        body = await request.json()
        password = str(body.get("password", ""))
    else:
        form = await request.form()
        password = str(form.get("password", ""))

    if not passwords_match(password, settings.app_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid password",
        )

    rate_limiter.reset(f"login:{ip}")
    set_session_cookie(response, settings)
    return MessageResponse(message="Logged in")


@router.post("/api/logout", response_model=MessageResponse)
async def api_logout(
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
) -> MessageResponse:
    clear_session_cookie(response, settings)
    return MessageResponse(message="Logged out")


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
):
    templates = get_templates(request)
    if is_authenticated(request, settings):
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": None},
    )


@router.post("/login", response_class=HTMLResponse)
async def login_form(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    password: Annotated[str, Form()],
):
    templates = get_templates(request)
    ip = client_ip(request)
    if not rate_limiter.is_allowed(
        f"login:{ip}",
        settings.login_rate_limit,
        settings.login_rate_window_seconds,
    ):
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Too many login attempts. Please wait and try again.",
            },
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    if not passwords_match(password, settings.app_password):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid password"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    rate_limiter.reset(f"login:{ip}")
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    set_session_cookie(response, settings)
    return response


@router.post("/logout")
async def logout_form(
    settings: Annotated[Settings, Depends(get_settings)],
):
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    clear_session_cookie(response, settings)
    return response
