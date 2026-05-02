from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.auth.routes import router as auth_router

app = FastAPI(title="MediaServer")
app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(auth_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse("/login", status_code=303)
