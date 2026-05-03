from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import SESSION_COOKIE, get_current_user_partial
from app.auth.passwords import hash_password, verify_password
from app.auth.sessions import create_session, delete_session, promote_session
from app.csrf import verify_csrf
from app.deps import get_db, render
from app.models import User

# Pre-computed bcrypt hash for constant-time login.
# When a username doesn't exist we still run verify_password against this hash
# so an attacker can't tell from timing whether the username is registered.
_DUMMY_HASH = "$2b$12$2c.5f53Q3NK9EuhHoCSFSuD6I6GXXAE9Vd654eSySWBtwDm.adhOC"

router = APIRouter()

PARTIAL_SESSION_TTL_DAYS = 1
FULL_SESSION_TTL_DAYS = 30
MIN_PASSWORD_LEN = 12


def _set_session_cookie(response, token: str):
    response.set_cookie(
        SESSION_COOKIE, token,
        httponly=True, secure=True, samesite="strict",
        path="/", max_age=FULL_SESSION_TTL_DAYS * 86400,
    )


@router.get("/login", response_class=HTMLResponse)
def login_get(request: Request) -> HTMLResponse:
    return render(request, "login.html", {"error": None})


@router.post("/login", response_model=None)
def login_post(
    request: Request,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    db: Annotated[Session, Depends(get_db)],
    _csrf: Annotated[None, Depends(verify_csrf)] = None,
) -> RedirectResponse | HTMLResponse:
    user = db.scalars(select(User).where(User.username == username)).first()
    hash_to_check = user.password_hash if user is not None else _DUMMY_HASH
    password_ok = verify_password(password, hash_to_check)
    if user is None or not password_ok:
        return render(
            request, "login.html", {"error": "Неверный логин или пароль"},
            status_code=401,
        )

    if user.must_change_password:
        token = create_session(db, user_id=user.id, ttl_days=PARTIAL_SESSION_TTL_DAYS, is_partial=True)
        target = "/change-password"
    else:
        token = create_session(db, user_id=user.id, ttl_days=FULL_SESSION_TTL_DAYS, is_partial=False)
        target = "/library"
    db.commit()

    response = RedirectResponse(target, status_code=303)
    _set_session_cookie(response, token)
    return response


@router.get("/change-password", response_class=HTMLResponse)
def change_password_get(
    request: Request,
    user: Annotated[User, Depends(get_current_user_partial)],
) -> HTMLResponse:
    return render(
        request, "change_password.html", {"user": user, "error": None}
    )


@router.post("/change-password", response_model=None)
def change_password_post(
    request: Request,
    new_password: Annotated[str, Form()],
    confirm: Annotated[str, Form()],
    user: Annotated[User, Depends(get_current_user_partial)],
    db: Annotated[Session, Depends(get_db)],
    _csrf: Annotated[None, Depends(verify_csrf)] = None,
) -> RedirectResponse | HTMLResponse:
    if len(new_password) < MIN_PASSWORD_LEN:
        return render(
            request,
            "change_password.html",
            {"user": user, "error": f"Пароль должен быть не короче {MIN_PASSWORD_LEN} символов"},
            status_code=400,
        )
    if new_password != confirm:
        return render(
            request,
            "change_password.html",
            {"user": user, "error": "Пароли не совпадают"},
            status_code=400,
        )

    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    token = request.cookies.get(SESSION_COOKIE) or ""
    promote_session(db, token, ttl_days=FULL_SESSION_TTL_DAYS)
    db.commit()
    return RedirectResponse("/library", status_code=303)


@router.post("/logout")
def logout(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    _csrf: Annotated[None, Depends(verify_csrf)] = None,
) -> RedirectResponse:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        delete_session(db, token)
        db.commit()
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, secure=True, samesite="strict")
    return response
