from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.deps import get_db, render
from app.models import MediaItem, User

router = APIRouter()


@router.get("/library", response_class=HTMLResponse)
async def library_page(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    items = db.scalars(select(MediaItem).order_by(MediaItem.added_at.desc())).all()
    return render(request, "library.html", {"user": user, "items": items})
