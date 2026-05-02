from typing import Callable

from fastapi import Depends, Request
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.db import make_engine, make_session_factory


def get_db_factory() -> sessionmaker[Session]:
    """Singleton-фабрика. Подменяется в тестах через app.dependency_overrides."""
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
