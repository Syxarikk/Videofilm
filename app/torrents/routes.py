import re
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
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

# Принимаем magnet-ссылку ИЛИ HTTP(S) URL (qBittorrent сам скачает .torrent по URL)
_MAGNET_OR_URL_RE = re.compile(
    r"^(magnet:\?xt=urn:btih:[a-fA-F0-9]{32,64}|https?://)",
    re.IGNORECASE,
)

# Максимум для .torrent файла (1MB — больше всякого здравого смысла; защита от DoS)
_MAX_TORRENT_FILE_BYTES = 1024 * 1024


def _is_bencode_dict(content: bytes) -> bool:
    """Проверка что байты выглядят как bencode-словарь — формат .torrent файла.

    Все валидные .torrent начинаются с 'd' (bencode dictionary) и заканчиваются на 'e'.
    Минимальный размер — десяток байтов (хеш + ключи), берём 11 для безопасности.
    """
    return len(content) >= 11 and content[:1] == b"d" and content[-1:] == b"e"


@api_router.post("")
async def add_torrent(
    user: Annotated[User, Depends(get_current_user)],
    qb: Annotated[QBittorrentClient, Depends(get_qbittorrent_client)],
    settings: Annotated[Settings, Depends(get_settings)],
    magnet: Annotated[str | None, Form()] = None,
    torrent_file: Annotated[UploadFile | None, File()] = None,
    _csrf: Annotated[None, Depends(verify_csrf)] = None,
):
    """Принимает один из трёх вариантов:

    1. magnet-ссылку (`magnet:?xt=urn:btih:...`)
    2. HTTP(S) URL .torrent-файла (qBittorrent сам скачает по URL)
    3. .torrent файл загруженный с компьютера (multipart upload)
    """
    save_path = f"{settings.media_root}/downloads"

    # 1. Файл (если загружен) — приоритет, потому что обычно явный выбор
    if torrent_file is not None and torrent_file.filename:
        content = await torrent_file.read()
        if len(content) > _MAX_TORRENT_FILE_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f".torrent файл слишком большой (>{_MAX_TORRENT_FILE_BYTES // 1024} КБ)",
            )
        if not _is_bencode_dict(content):
            raise HTTPException(
                status_code=400,
                detail="Не похоже на .torrent файл (ожидается bencode-формат)",
            )
        try:
            qb.add_torrent_file(content, torrent_file.filename, save_path=save_path)
        except QBittorrentError as e:
            raise HTTPException(status_code=503, detail=f"qBittorrent недоступен: {e}")
        return RedirectResponse("/downloads", status_code=303)

    # 2. magnet или URL
    if magnet:
        magnet = magnet.strip()
        if not _MAGNET_OR_URL_RE.match(magnet):
            raise HTTPException(
                status_code=400,
                detail="Не похоже на magnet-ссылку или URL .torrent-файла",
            )
        try:
            qb.add_magnet(magnet, save_path=save_path)
        except QBittorrentError as e:
            raise HTTPException(status_code=503, detail=f"qBittorrent недоступен: {e}")
        return RedirectResponse("/downloads", status_code=303)

    # 3. Ничего не передано
    raise HTTPException(
        status_code=400,
        detail="Нужно указать magnet-ссылку, URL .torrent-файла или загрузить .torrent файл",
    )


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
def torrents_status(
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
def add_torrent_page(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
):
    return render(request, "add_torrent.html", {"user": user})


@router.get("/downloads", response_class=HTMLResponse)
def downloads_page(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
):
    return render(request, "downloads.html", {"user": user})
