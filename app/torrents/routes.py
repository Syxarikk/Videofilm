import re
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.config import Settings, get_settings
from app.csrf import verify_csrf
from app.deps import get_db, get_qbittorrent_client
from app.models import User
from app.torrents.client import QBittorrentClient, QBittorrentError

router = APIRouter()
api_router = APIRouter(prefix="/api/torrents")

_MAGNET_RE = re.compile(r"^magnet:\?xt=urn:btih:[a-fA-F0-9]{32,64}", re.IGNORECASE)


@api_router.post("")
async def add_torrent(
    magnet: Annotated[str, Form()],
    user: Annotated[User, Depends(get_current_user)],
    qb: Annotated[QBittorrentClient, Depends(get_qbittorrent_client)],
    settings: Annotated[Settings, Depends(get_settings)],
    _csrf: Annotated[None, Depends(verify_csrf)] = None,
):
    magnet = magnet.strip()
    if not _MAGNET_RE.match(magnet):
        raise HTTPException(status_code=400, detail="Не похоже на magnet-ссылку")
    save_path = f"{settings.media_root}/downloads"
    try:
        qb.add_magnet(magnet, save_path=save_path)
    except QBittorrentError as e:
        raise HTTPException(status_code=503, detail=f"qBittorrent недоступен: {e}")
    return RedirectResponse("/downloads", status_code=303)
