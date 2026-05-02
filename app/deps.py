from functools import lru_cache
from typing import Callable

from fastapi import Depends, Request
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
