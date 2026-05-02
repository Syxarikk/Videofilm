from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import Engine as _Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


def make_engine(database_url: str) -> Engine:
    if database_url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
        # In-memory SQLite must share one connection across threads;
        # otherwise each thread gets a fresh empty DB.
        if ":memory:" in database_url or database_url == "sqlite://":
            return create_engine(
                database_url,
                connect_args=connect_args,
                poolclass=StaticPool,
                future=True,
            )
        return create_engine(database_url, connect_args=connect_args, future=True)
    return create_engine(database_url, future=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


@event.listens_for(_Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    """SQLite по умолчанию не enforce'ит foreign keys; включаем явно для каждого нового подключения."""
    # Только для SQLite (модуль sqlite3 / pysqlite)
    module_name = type(dbapi_connection).__module__
    if "sqlite" not in module_name.lower():
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
