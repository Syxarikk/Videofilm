from sqlalchemy import text
from app.db import make_engine, make_session_factory


def test_make_engine_creates_in_memory_sqlite():
    engine = make_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1")).scalar()
    assert result == 1


def test_session_factory_yields_session():
    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)
    with factory() as session:
        result = session.execute(text("SELECT 42")).scalar()
    assert result == 42
