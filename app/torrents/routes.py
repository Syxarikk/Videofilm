import re
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.config import Settings, get_settings
from app.csrf import verify_csrf
from app.deps import get_db, get_qbittorrent_client, render
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


def _format_speed(bytes_per_sec: int) -> str:
    if bytes_per_sec < 1024:
        return f"{bytes_per_sec} B/s"
    if bytes_per_sec < 1024 * 1024:
        return f"{bytes_per_sec / 1024:.1f} KB/s"
    return f"{bytes_per_sec / (1024 * 1024):.1f} MB/s"


def _format_eta(seconds: int) -> str:
    if seconds < 0 or seconds > 365 * 24 * 3600:
        return "—"
    h, rem = divmod(seconds, 3600)
    m, _ = divmod(rem, 60)
    if h > 0:
        return f"{h}ч {m:02d}м"
    return f"{m}м"


@api_router.get("/status")
async def torrents_status(
    user: Annotated[User, Depends(get_current_user)],
    qb: Annotated[QBittorrentClient, Depends(get_qbittorrent_client)],
):
    try:
        torrents = qb.list_torrents()
    except QBittorrentError as e:
        raise HTTPException(status_code=503, detail=f"qBittorrent недоступен: {e}")
    return [
        {
            "hash": t.hash,
            "name": t.name,
            "progress_percent": int(t.progress * 100),
            "speed_human": _format_speed(t.dlspeed),
            "eta_human": _format_eta(t.eta_seconds),
            "state": t.state,
            "is_complete": t.is_complete,
        }
        for t in torrents
    ]


@router.get("/add-torrent", response_class=HTMLResponse)
async def add_torrent_page(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
):
    return render(request, "add_torrent.html", {"user": user})


@router.get("/downloads", response_class=HTMLResponse)
async def downloads_page(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
):
    return render(request, "downloads.html", {"user": user})
