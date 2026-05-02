from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.backup_codes import verify_and_consume
from app.auth.deps import SESSION_COOKIE, get_current_user_partial
from app.auth.passwords import verify_password
from app.auth.sessions import create_session, promote_session
from app.auth.totp import _derive_key, decrypt_secret, verify_code
from app.config import get_settings
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


@router.get("/verify-totp", response_class=HTMLResponse)
async def verify_totp_get(
    request: Request,
    user: Annotated[User, Depends(get_current_user_partial)],
) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "verify_totp.html", {"user": user, "error": None}
    )


@router.post("/verify-totp", response_model=None)
async def verify_totp_post(
    request: Request,
    code: Annotated[str, Form()],
    user: Annotated[User, Depends(get_current_user_partial)],
    db: Annotated[Session, Depends(get_db)],
) -> RedirectResponse | HTMLResponse:
    code = code.strip()
    settings = get_settings()
    key = _derive_key(settings.session_secret)

    ok = False
    if user.totp_secret_encrypted:
        try:
            secret = decrypt_secret(user.totp_secret_encrypted, key)
        except Exception:
            secret = None
        if secret is not None and verify_code(secret, code):
            ok = True
    if not ok:
        if verify_and_consume(db, user.id, code):
            ok = True
            db.commit()

    if not ok:
        return templates.TemplateResponse(
            request, "verify_totp.html", {"user": user, "error": "Неверный код"},
            status_code=401,
        )

    token = request.cookies.get(SESSION_COOKIE) or ""
    promote_session(db, token, ttl_days=FULL_SESSION_TTL_DAYS)
    db.commit()
    return RedirectResponse("/library", status_code=303)
