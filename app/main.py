from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest

from app.auth.routes import router as auth_router
from app.library.routes import router as library_router

app = FastAPI(title="MediaServer")
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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse("/login", status_code=303)
