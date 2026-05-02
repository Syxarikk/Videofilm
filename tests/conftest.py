import os

import pytest
from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient

from app.db import Base, make_engine, make_session_factory


@pytest.fixture(autouse=True)
def _clear_caches():
    yield
    from app.config import get_settings
    from app.deps import get_db_factory
    get_settings.cache_clear()
    get_db_factory.cache_clear()
    from app.deps import get_qbittorrent_client
    get_qbittorrent_client.cache_clear()


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "x" * 64)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MEDIA_ROOT", "/tmp/media")
    monkeypatch.setenv("QBITTORRENT_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("QBITTORRENT_USERNAME", "admin")
    monkeypatch.setenv("QBITTORRENT_PASSWORD", "secret")
    monkeypatch.setenv("TOTP_ISSUER", "TestSrv")


@pytest.fixture
def db_factory() -> sessionmaker:
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


@pytest.fixture
def client(db_factory):
    """TestClient with overridden DB dependency. Used by integration tests."""
    from app.main import app
    from app.deps import get_db_factory

    app.dependency_overrides[get_db_factory] = lambda: db_factory
    with TestClient(app, follow_redirects=False) as c:
        yield c
    app.dependency_overrides.clear()
    c.cookies.clear()  # на всякий случай — изоляция между тестами


@pytest.fixture
def csrf_for(client):
    """Возвращает функцию: csrf_for(cookie) → токен, валидный при той же session-cookie."""
    from app.csrf import generate_token

    def _make(cookie: str | None) -> str:
        return generate_token(cookie or "")

    return _make
