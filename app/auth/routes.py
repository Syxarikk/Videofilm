import base64
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.backup_codes import generate_codes, hash_code as bc_hash_code, verify_and_consume
from app.auth.deps import SESSION_COOKIE, get_current_user_partial
from app.auth.passwords import hash_password, verify_password
from app.auth.sessions import create_session, delete_session, promote_session
from app.auth.totp import (
    _derive_key,
    decrypt_secret,
    encrypt_secret,
    generate_secret,
    provisioning_uri,
    qr_png_bytes,
    verify_code,
)
from app.config import get_settings
from app.csrf import verify_csrf
from app.deps import get_db, render
from app.models import BackupCode, User

# Pre-computed bcrypt hash for constant-time login.
# Когда пользователя с таким логином нет, мы всё равно делаем verify_password
# против этого хеша — иначе атакующий по таймингу узнает, есть ли логин в системе.
# Hash = bcrypt cost=12 of a canary string we never use.
_DUMMY_HASH = "$2b$12$2c.5f53Q3NK9EuhHoCSFSuD6I6GXXAE9Vd654eSySWBtwDm.adhOC"

router = APIRouter()

PARTIAL_SESSION_TTL_DAYS = 1
FULL_SESSION_TTL_DAYS = 30


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
    # Constant-time: всегда выполняем bcrypt, даже если юзера нет
    hash_to_check = user.password_hash if user is not None else _DUMMY_HASH
    password_ok = verify_password(password, hash_to_check)
    if user is None or not password_ok:
        return render(
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
def verify_totp_get(
    request: Request,
    user: Annotated[User, Depends(get_current_user_partial)],
) -> HTMLResponse:
    return render(
        request, "verify_totp.html", {"user": user, "error": None}
    )


@router.post("/verify-totp", response_model=None)
def verify_totp_post(
    request: Request,
    code: Annotated[str, Form()],
    user: Annotated[User, Depends(get_current_user_partial)],
    db: Annotated[Session, Depends(get_db)],
    _csrf: Annotated[None, Depends(verify_csrf)] = None,
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
        return render(
            request, "verify_totp.html", {"user": user, "error": "Неверный код"},
            status_code=401,
        )

    token = request.cookies.get(SESSION_COOKIE) or ""
    promote_session(db, token, ttl_days=FULL_SESSION_TTL_DAYS)
    db.commit()
    return RedirectResponse("/library", status_code=303)


MIN_PASSWORD_LEN = 12


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
    db.commit()
    return RedirectResponse("/enroll-2fa", status_code=303)


@router.get("/enroll-2fa", response_class=HTMLResponse)
def enroll_2fa_get(
    request: Request,
    user: Annotated[User, Depends(get_current_user_partial)],
    db: Annotated[Session, Depends(get_db)],
) -> HTMLResponse:
    settings = get_settings()
    key = _derive_key(settings.session_secret)

    if user.totp_secret_encrypted is None:
        secret = generate_secret()
        user.totp_secret_encrypted = encrypt_secret(secret, key)
    else:
        secret = decrypt_secret(user.totp_secret_encrypted, key)

    # Backup-коды: генерируем при первом GET'е этой страницы для пользователя.
    existing = db.scalars(select(BackupCode).where(BackupCode.user_id == user.id)).all()
    backup_plain: list[str] | None = None
    if not existing:
        backup_plain = generate_codes()
        for c in backup_plain:
            db.add(BackupCode(user_id=user.id, code_hash=bc_hash_code(c)))

    db.commit()

    uri = provisioning_uri(secret, user.username, settings.totp_issuer)
    qr_b64 = base64.b64encode(qr_png_bytes(uri)).decode("ascii")

    return render(
        request,
        "enroll_2fa.html",
        {
            "user": user,
            "qr_data_uri": f"data:image/png;base64,{qr_b64}",
            "secret": secret,
            "backup_codes": backup_plain,  # None если уже сгенерированы (а значит, страница перезагружена)
            "error": None,
        },
    )


@router.post("/enroll-2fa", response_model=None)
def enroll_2fa_post(
    request: Request,
    code: Annotated[str, Form()],
    user: Annotated[User, Depends(get_current_user_partial)],
    db: Annotated[Session, Depends(get_db)],
    _csrf: Annotated[None, Depends(verify_csrf)] = None,
) -> RedirectResponse | HTMLResponse:
    if user.totp_secret_encrypted is None:
        return RedirectResponse("/enroll-2fa", status_code=303)

    settings = get_settings()
    key = _derive_key(settings.session_secret)
    secret = decrypt_secret(user.totp_secret_encrypted, key)

    if not verify_code(secret, code.strip()):
        return render(
            request,
            "enroll_2fa.html",
            {
                "user": user,
                "qr_data_uri": None,
                "secret": None,
                "backup_codes": None,
                "error": "Неверный код. Проверьте время на телефоне и попробуйте снова.",
            },
            status_code=400,
        )

    user.totp_enabled = True
    db.commit()

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
