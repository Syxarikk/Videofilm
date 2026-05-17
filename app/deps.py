from functools import lru_cache
from typing import Callable

from fastapi import Depends, Request
from fastapi import Request as _Req
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.db import make_engine, make_session_factory


@lru_cache(maxsize=1)
def get_db_factory() -> sessionmaker[Session]:
    """Singleton: один engine на всё приложение."""
    s = get_settings()
    engine = make_engine(s.database_url)
    return make_session_factory(engine)


def get_db(factory: sessionmaker[Session] = Depends(get_db_factory)) -> Session:
    db = factory()
    try:
        yield db
    finally:
        db.close()


def get_app_settings() -> Settings:
    return get_settings()


_TEMPLATES = Jinja2Templates(directory="templates")


def render(
    request: _Req,
    template_name: str,
    context: dict | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """Рендеринг шаблона с авто-инжектом csrf_token из текущей сессии.
    Все роуты должны использовать этот хелпер вместо прямого Jinja2Templates."""
    from app.auth.deps import SESSION_COOKIE
    from app.csrf import generate_token

    session_key = request.cookies.get(SESSION_COOKIE) or ""
    full = {"csrf_token": generate_token(session_key)}
    if context:
        full.update(context)
    return _TEMPLATES.TemplateResponse(request, template_name, full, status_code=status_code)


from functools import lru_cache as _lru_cache_qb
from app.torrents.client import QBittorrentClient


@_lru_cache_qb(maxsize=1)
def get_qbittorrent_client() -> QBittorrentClient:
    s = get_settings()
    return QBittorrentClient(s.qbittorrent_url, s.qbittorrent_username, s.qbittorrent_password)


@lru_cache(maxsize=1)
def get_tmdb_client():
    from app.metadata.tmdb import TmdbClient
    s = get_settings()
    if not s.tmdb_api_key:
        return None
    return TmdbClient(s.tmdb_api_key)


@lru_cache(maxsize=1)
def get_kinopoisk_client():
    from app.metadata.kinopoisk import KinopoiskClient
    s = get_settings()
    if not s.kinopoisk_api_key:
        return None
    return KinopoiskClient(s.kinopoisk_api_key)
