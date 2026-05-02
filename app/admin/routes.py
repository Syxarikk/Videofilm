import re
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import require_admin
from app.auth.passwords import hash_password
from app.deps import get_db
from app.models import User

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="templates")


@router.get("/users", response_class=HTMLResponse)
async def list_users(
    request: Request,
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> HTMLResponse:
    users = db.scalars(select(User).order_by(User.id)).all()
    return templates.TemplateResponse(
        request,
        "admin_users.html",
        {"user": admin, "users": users, "created_user": None, "temp_password": None},
    )


USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,32}$")


@router.post("/users", response_class=HTMLResponse)
async def create_user(
    request: Request,
    username: Annotated[str, Form()],
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    is_admin: Annotated[str | None, Form()] = None,
) -> HTMLResponse:
    username = username.strip()
    users = db.scalars(select(User).order_by(User.id)).all()

    if not USERNAME_RE.match(username):
        return templates.TemplateResponse(
            request,
            "admin_users.html",
            {
                "user": admin,
                "users": users,
                "created_user": None,
                "temp_password": None,
                "error": "Логин: 3–32 символа, только латиница, цифры, _.",
            },
            status_code=400,
        )

    existing = db.scalars(select(User).where(User.username == username)).first()
    if existing is not None:
        return templates.TemplateResponse(
            request,
            "admin_users.html",
            {
                "user": admin,
                "users": users,
                "created_user": None,
                "temp_password": None,
                "error": "Логин уже занят.",
            },
            status_code=400,
        )

    temp_password = secrets.token_urlsafe(12)
    new_user = User(
        username=username,
        password_hash=hash_password(temp_password),
        must_change_password=True,
        totp_enabled=False,
        is_admin=bool(is_admin),
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    users = db.scalars(select(User).order_by(User.id)).all()
    return templates.TemplateResponse(
        request,
        "admin_users.html",
        {
            "user": admin,
            "users": users,
            "created_user": new_user,
            "temp_password": temp_password,
        },
    )


@router.post("/users/{user_id}/delete")
async def delete_user(
    user_id: int,
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> RedirectResponse:
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="Нельзя удалить самого себя")
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404)
    db.delete(target)
    db.commit()
    return RedirectResponse("/admin/users", status_code=303)
