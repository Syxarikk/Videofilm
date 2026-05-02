"""Скачивание оригинального файла с поддержкой Range-запросов."""
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.deps import get_db
from app.models import MediaItem, User


api_router = APIRouter(prefix="/api/download")


def _parse_range(header: str, file_size: int) -> tuple[int, int] | None:
    """Парсит 'bytes=START-END' (END необязателен). Возвращает (start, end) или None если невалидно."""
    if not header.startswith("bytes="):
        return None
    spec = header[len("bytes="):]
    if "-" not in spec:
        return None
    start_s, end_s = spec.split("-", 1)
    try:
        start = int(start_s) if start_s else 0
        end = int(end_s) if end_s else file_size - 1
    except ValueError:
        return None
    if start < 0 or end >= file_size or start > end:
        return None
    return start, end


@api_router.get("/{media_id}")
def download(
    media_id: int,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    media = db.get(MediaItem, media_id)
    if media is None:
        raise HTTPException(status_code=404)
    path = Path(media.file_path)
    if not path.exists():
        raise HTTPException(status_code=410, detail="файл отсутствует на диске")

    # Имя файла для скачивания: оригинальное имя файла, ASCII fallback + RFC 5987 utf-8
    filename = path.name
    ascii_fallback = "".join(c if c.isascii() and c.isprintable() else "_" for c in filename)
    cd = (
        f'attachment; filename="{ascii_fallback}"; '
        f"filename*=UTF-8''{quote(filename)}"
    )

    range_header = request.headers.get("range")
    file_size = path.stat().st_size

    if range_header is None:
        return FileResponse(
            str(path),
            media_type="application/octet-stream",
            headers={"Content-Disposition": cd, "Accept-Ranges": "bytes"},
        )

    parsed = _parse_range(range_header, file_size)
    if parsed is None:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{file_size}"})

    start, end = parsed
    length = end - start + 1

    def _iter():
        with path.open("rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Content-Length": str(length),
        "Content-Disposition": cd,
        "Accept-Ranges": "bytes",
        "Content-Type": "application/octet-stream",
    }
    return StreamingResponse(_iter(), status_code=206, headers=headers)
