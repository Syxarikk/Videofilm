from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import require_admin
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
