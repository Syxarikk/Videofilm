from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.auth.deps import get_current_user
from app.models import User

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/library", response_class=HTMLResponse)
async def library_page(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "library.html", {"user": user, "items": []}
    )
