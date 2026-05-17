import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest

from app.admin.routes import router as admin_router
from app.auth.routes import router as auth_router
from app.deps import get_db_factory, get_kinopoisk_client, get_qbittorrent_client, get_tmdb_client
from app.download.routes import api_router as download_api_router
from app.library.routes import router as library_router
from app.streaming.routes import api_router as streaming_api_router, progress_router as streaming_progress_router
from app.streaming.watchdog import watchdog_loop
from app.torrents.routes import api_router as torrents_api_router, router as torrents_router
from app.torrents.scanner import scanner_loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: запускаем фоновые задачи — scanner и watchdog.
    # В тестах TestClient() запустит lifespan, но фоновые таски ловят все исключения.
    scanner_task = asyncio.create_task(
        scanner_loop(
            get_qbittorrent_client(), get_db_factory(),
            interval_seconds=10.0,
            tmdb=get_tmdb_client(), kinopoisk=get_kinopoisk_client(),
        )
    )
    watchdog_task = asyncio.create_task(watchdog_loop())
    try:
        yield
    finally:
        # 1. Остановить фоновые задачи
        for t in (scanner_task, watchdog_task):
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

        # 2. Убить все активные ffmpeg-стримы (idle_seconds=0 → все попадают в выборку)
        from app.streaming.stream_registry import get_registry
        from app.streaming.watchdog import sweep_idle
        try:
            sweep_idle(get_registry(), idle_seconds=0.0)
        except Exception:
            pass

        # 3. Закрыть httpx-клиент qBittorrent
        try:
            get_qbittorrent_client().close()
        except Exception:
            pass


app = FastAPI(title="MediaServer", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")


class AuthRedirectMiddleware(BaseHTTPMiddleware):
    """Если ответ 401 на GET → редиректим на /login (для UX). API-эндпоинты пусть отдают 401 как есть."""

    async def dispatch(self, request: StarletteRequest, call_next):
        response = await call_next(request)
        if (
            response.status_code == 401
            and request.method == "GET"
            and not request.url.path.startswith("/api/")
            and request.url.path not in ("/login", "/health")
        ):
            return RedirectResponse("/login", status_code=303)
        return response


app.add_middleware(AuthRedirectMiddleware)
app.include_router(auth_router)
app.include_router(library_router)
app.include_router(admin_router)
app.include_router(torrents_api_router)
app.include_router(torrents_router)
app.include_router(streaming_api_router)
app.include_router(streaming_progress_router)
app.include_router(download_api_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse("/login", status_code=303)
