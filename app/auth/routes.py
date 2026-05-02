from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import SESSION_COOKIE
from app.auth.passwords import verify_password
from app.auth.sessions import create_session
from app.deps import get_db
from app.models import User

router = APIRouter()
templates = Jinja2Templates(directory="templates")

PARTIAL_SESSION_TTL_DAYS = 1
FULL_SESSION_TTL_DAYS = 30


def _set_session_cookie(response, token: str):
    response.set_cookie(
        SESSION_COOKIE, token,
        httponly=True, secure=True, samesite="strict",
        path="/", max_age=FULL_SESSION_TTL_DAYS * 86400,
    )


@router.get("/login", response_class=HTMLResponse)
async def login_get(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login", response_model=None)
async def login_post(
    request: Request,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    db: Annotated[Session, Depends(get_db)],
) -> RedirectResponse | HTMLResponse:
    user = db.scalars(select(User).where(User.username == username)).first()
    if user is None or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request, "login.html", {"error": "Неверный логин или пароль"},
            status_code=401,
        )

    token = create_session(db, user_id=user.id, ttl_days=PARTIAL_SESSION_TTL_DAYS, is_partial=True)
    db.commit()

    if user.must_change_password:
        target = "/change-password"
    elif not user.totp_enabled:
        target = "/enroll-2fa"
    else:
        target = "/verify-totp"

    response = RedirectResponse(target, status_code=303)
    _set_session_cookie(response, token)
    return response
