import logging
import shutil
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.csrf import verify_csrf
from app.deps import get_db, get_kinopoisk_client, get_qbittorrent_client, get_tmdb_client, render
from app.models import Genre, MediaItem, User, WatchProgress
from app.streaming.ffmpeg_runner import kill as kill_ffmpeg
from app.streaming.stream_registry import get_registry
from app.torrents.client import QBittorrentClient, QBittorrentError

log = logging.getLogger(__name__)

router = APIRouter()


WATCHED_RATIO = 0.65


def _compute_status(progress: WatchProgress | None, duration: int | None) -> str:
    if progress is None or progress.position_seconds <= 0:
        return "not_started"
    if duration is None:
        return "in_progress"
    if progress.position_seconds >= WATCHED_RATIO * duration:
        return "watched"
    return "in_progress"


@router.get("/library", response_class=HTMLResponse)
def library_page(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    q: str | None = None,
    kind: str | None = None,
    genre: str | None = None,
    sort: str = "new",
    status: str | None = None,
):
    stmt = select(MediaItem)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(MediaItem.title.ilike(like),
                              MediaItem.description.ilike(like)))
    if kind:
        stmt = stmt.where(MediaItem.kind == kind)
    if genre:
        stmt = stmt.where(MediaItem.genres.any(Genre.name == genre))

    if sort == "old":
        stmt = stmt.order_by(MediaItem.added_at.asc())
    elif sort == "title_asc":
        stmt = stmt.order_by(MediaItem.title.asc())
    elif sort == "year_desc":
        stmt = stmt.order_by(MediaItem.year.desc().nullslast(), MediaItem.title.asc())
    elif sort == "year_asc":
        stmt = stmt.order_by(MediaItem.year.asc().nullsfirst(), MediaItem.title.asc())
    else:
        stmt = stmt.order_by(MediaItem.added_at.desc())

    items = db.scalars(stmt).unique().all()

    progresses = {
        wp.media_id: wp
        for wp in db.scalars(
            select(WatchProgress).where(WatchProgress.user_id == user.id)
        )
    }

    annotated = []
    for it in items:
        wp = progresses.get(it.id)
        st = _compute_status(wp, it.duration_seconds)
        if status and st != status:
            continue
        annotated.append({"item": it, "status": st,
                          "position": wp.position_seconds if wp else 0})

    all_genres = [g.name for g in db.scalars(select(Genre).order_by(Genre.name))]

    template = "_library_grid.html" if request.headers.get("HX-Request") else "library.html"
    return render(request, template, {
        "user": user,
        "items": annotated,
        "filters": {"q": q or "", "kind": kind or "", "genre": genre or "",
                    "sort": sort, "status": status or ""},
        "all_genres": all_genres,
    })


@router.get("/media/{media_id}", response_class=HTMLResponse)
def media_page(
    media_id: int,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    item = db.get(MediaItem, media_id)
    if item is None:
        raise HTTPException(status_code=404)

    # Лениво дозаполняем duration_seconds и audio_tracks для старых записей.
    needs_commit = False
    if item.duration_seconds is None:
        from app.metadata.ffprobe import get_duration_seconds
        dur = get_duration_seconds(item.file_path)
        if dur is not None:
            item.duration_seconds = dur
            needs_commit = True
    if item.audio_tracks is None:
        from app.metadata.ffprobe import probe_audio_tracks
        tracks = probe_audio_tracks(item.file_path)
        item.audio_tracks = [
            {"index": a.index, "codec": a.codec, "language": a.language,
             "title": a.title, "channels": a.channels}
            for a in tracks
        ]
        needs_commit = True
    if needs_commit:
        db.commit()

    progress = db.scalars(
        select(WatchProgress).where(
            WatchProgress.user_id == user.id,
            WatchProgress.media_id == media_id,
        )
    ).first()
    saved_position = progress.position_seconds if progress else 0
    saved_audio_track = progress.audio_track_index if progress else None

    return render(request, "media.html", {
        "user": user,
        "item": item,
        "saved_position_seconds": saved_position,
        "saved_audio_track_index": saved_audio_track,
    })


@router.post("/api/media/{media_id}/delete")
def delete_media(
    media_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    qb: Annotated[QBittorrentClient, Depends(get_qbittorrent_client)],
    _csrf: Annotated[None, Depends(verify_csrf)] = None,
):
    item = db.get(MediaItem, media_id)
    if item is None:
        raise HTTPException(status_code=404)

    # 1. Убить все ffmpeg-процессы для этого media_id (любого юзера)
    reg = get_registry()
    for handle in list(reg.all_streams()):
        if handle.media_id == media_id and handle.process is not None:
            kill_ffmpeg(handle.process)
            reg.unregister(handle.media_id, handle.user_id)
            shutil.rmtree(handle.work_dir, ignore_errors=True)

    # 2. Сказать qBittorrent удалить торрент с файлами
    try:
        qb.delete_torrent(item.torrent_hash, delete_files=True)
    except QBittorrentError as e:
        # qBittorrent упал — продолжаем; файлы можно потом вычистить вручную
        log.warning(
            "delete_media: qBittorrent unreachable for torrent %s, files orphaned: %s",
            item.torrent_hash, e,
        )

    # 3. Удалить из БД (CASCADE снесёт watch_progress)
    db.delete(item)
    db.commit()

    return RedirectResponse("/library", status_code=303)


# === Edit form & endpoint ===

@router.get("/api/media/{media_id}/edit-form", response_class=HTMLResponse)
def edit_form(
    media_id: int,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    item = db.get(MediaItem, media_id)
    if item is None:
        raise HTTPException(status_code=404)
    all_genres = [g.name for g in db.scalars(select(Genre).order_by(Genre.name))]
    return render(request, "_media_edit_modal.html", {
        "user": user, "item": item, "all_genres": all_genres,
    })


@router.post("/api/media/{media_id}/edit")
def edit_media(
    media_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    title: Annotated[str, Form()],
    kind: Annotated[str, Form()],
    description: Annotated[str, Form()] = "",
    genres: Annotated[str, Form()] = "",
    poster_url: Annotated[str, Form()] = "",
    _csrf: Annotated[None, Depends(verify_csrf)] = None,
):
    item = db.get(MediaItem, media_id)
    if item is None:
        raise HTTPException(status_code=404)

    item.title = title.strip() or item.title
    item.description = description.strip() or None
    if kind in {"movie", "series", "cartoon", "anime", "documentary", "show", "other"}:
        item.kind = kind
    item.poster_url = poster_url.strip() or None

    new_names = [g.strip() for g in genres.split(",") if g.strip()]
    item.genres.clear()
    for name in new_names:
        existing = db.scalars(select(Genre).where(Genre.name == name)).first()
        if existing is None:
            existing = Genre(name=name)
            db.add(existing); db.flush()
        item.genres.append(existing)

    item.match_status = "manual"
    item.match_source = "manual"
    db.commit()

    return Response(status_code=204, headers={"HX-Redirect": f"/media/{media_id}"})


# === Re-match endpoints ===

@router.get("/api/media/{media_id}/match/search-form", response_class=HTMLResponse)
def match_search_form(
    media_id: int,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    item = db.get(MediaItem, media_id)
    if item is None:
        raise HTTPException(status_code=404)
    return render(request, "_match_dialog.html", {"user": user, "item": item, "results": None})


@router.post("/api/media/{media_id}/match/search", response_class=HTMLResponse)
def match_search(
    media_id: int,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    query: Annotated[str, Form()],
    year: Annotated[str, Form()] = "",
    kind: Annotated[str, Form()] = "",
    _csrf: Annotated[None, Depends(verify_csrf)] = None,
):
    item = db.get(MediaItem, media_id)
    if item is None:
        raise HTTPException(status_code=404)

    parsed_year = int(year) if year.strip().isdigit() else None
    hint = "tv" if kind == "series" else (
        "movie" if kind in ("movie", "cartoon", "anime", "documentary", "show", "other") else None
    )

    results = []
    tmdb = get_tmdb_client()
    if tmdb is not None:
        raw = tmdb.search(query, year=parsed_year, kind_hint=hint)
        for r in raw[:10]:
            results.append({
                "source": "tmdb",
                "external_id": r.get("id"),
                "title": r.get("title") or r.get("name") or "(?)",
                "year": (r.get("release_date") or r.get("first_air_date") or "")[:4] or None,
                "poster": (("https://image.tmdb.org/t/p/w92" + r["poster_path"])
                           if r.get("poster_path") else None),
                "kind": "tv" if (r.get("media_type") == "tv" or hint == "tv") else "movie",
            })

    kp = get_kinopoisk_client()
    if kp is not None and kp.quota_ok():
        raw = kp.search(query, year=parsed_year)
        for r in raw[:10]:
            results.append({
                "source": "kinopoisk",
                "external_id": r.get("filmId") or r.get("kinopoiskId"),
                "title": r.get("nameRu") or r.get("nameOriginal") or "(?)",
                "year": str(r.get("year")) if r.get("year") else None,
                "poster": r.get("posterUrlPreview") or r.get("posterUrl"),
                "kind": r.get("type", "FILM"),
            })

    return render(request, "_match_dialog.html", {
        "user": user, "item": item, "results": results, "query": query,
    })


@router.post("/api/media/{media_id}/match/apply")
def match_apply(
    media_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    source: Annotated[str, Form()],
    external_id: Annotated[int, Form()],
    _csrf: Annotated[None, Depends(verify_csrf)] = None,
):
    item = db.get(MediaItem, media_id)
    if item is None:
        raise HTTPException(status_code=404)

    match = None
    if source == "tmdb":
        client = get_tmdb_client()
        if client is None:
            raise HTTPException(status_code=400, detail="TMDB не сконфигурирован")
        match = client.get_movie(external_id) or client.get_tv(external_id)
    elif source == "kinopoisk":
        client = get_kinopoisk_client()
        if client is None:
            raise HTTPException(status_code=400, detail="Kinopoisk не сконфигурирован")
        match = client.get_film(external_id)
    else:
        raise HTTPException(status_code=400, detail="unknown source")

    if match is None:
        raise HTTPException(status_code=502, detail="не удалось получить детали")

    item.title = match.title
    item.description = match.description
    item.poster_url = match.poster_url
    item.year = match.year
    item.kind = match.kind
    if source == "tmdb":
        item.tmdb_id = match.external_id
        item.kinopoisk_id = None
    else:
        item.kinopoisk_id = match.external_id
        item.tmdb_id = None
    item.match_source = source
    item.match_status = "matched"

    item.genres.clear()
    for gname in match.genres:
        normalized = gname.strip()
        if not normalized:
            continue
        existing = db.scalars(select(Genre).where(Genre.name == normalized)).first()
        if existing is None:
            existing = Genre(name=normalized); db.add(existing); db.flush()
        item.genres.append(existing)

    db.commit()
    return Response(status_code=204, headers={"HX-Redirect": f"/media/{media_id}"})
