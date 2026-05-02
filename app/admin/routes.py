import re
import secrets
import shutil
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import require_admin
from app.auth.passwords import hash_password
from app.csrf import verify_csrf
from app.deps import get_db, get_qbittorrent_client, render
from app.models import User
from app.streaming.stream_registry import get_registry
from app.torrents.client import QBittorrentClient, QBittorrentError

router = APIRouter(prefix="/admin")


@router.get("/users", response_class=HTMLResponse)
def list_users(
    request: Request,
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> HTMLResponse:
    users = db.scalars(select(User).order_by(User.id)).all()
    return render(
        request,
        "admin_users.html",
        {"user": admin, "users": users, "created_user": None, "temp_password": None},
    )


USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,32}$")


@router.post("/users", response_class=HTMLResponse)
def create_user(
    request: Request,
    username: Annotated[str, Form()],
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    is_admin: Annotated[str | None, Form()] = None,
    _csrf: Annotated[None, Depends(verify_csrf)] = None,
) -> HTMLResponse:
    username = username.strip()
    users = db.scalars(select(User).order_by(User.id)).all()

    if not USERNAME_RE.match(username):
        return render(
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
        return render(
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
    return render(
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
def delete_user(
    user_id: int,
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    _csrf: Annotated[None, Depends(verify_csrf)] = None,
) -> RedirectResponse:
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="Нельзя удалить самого себя")
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404)
    db.delete(target)
    db.commit()
    return RedirectResponse("/admin/users", status_code=303)


@router.get("/health", response_class=HTMLResponse)
def health_page(
    request: Request,
    admin: Annotated[User, Depends(require_admin)],
    qb: Annotated[QBittorrentClient, Depends(get_qbittorrent_client)],
):
    # Диск
    try:
        d_root = shutil.disk_usage("/")
        disk_root = {
            "free_gb": round(d_root.free / (1024 ** 3), 1),
            "total_gb": round(d_root.total / (1024 ** 3), 1),
            "percent_used": round((1 - d_root.free / d_root.total) * 100, 1),
        }
    except Exception as e:
        disk_root = {"error": str(e)}

    # qBittorrent
    try:
        torrents = qb.list_torrents()
        qb_status = {"reachable": True, "active_torrents": len(torrents)}
    except QBittorrentError as e:
        qb_status = {"reachable": False, "error": str(e)}

    # Активные стримы
    streams = []
    for h in get_registry().all_streams():
        streams.append({
            "media_id": h.media_id, "user_id": h.user_id,
            "work_dir": h.work_dir,
            "alive": h.process is not None and (
                h.process.poll() is None if hasattr(h.process, "poll") else False
            ),
        })

    return render(request, "admin_health.html", {
        "user": admin,
        "disk_root": disk_root,
        "qb": qb_status,
        "streams": streams,
    })
