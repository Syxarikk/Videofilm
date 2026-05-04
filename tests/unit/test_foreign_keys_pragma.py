from sqlalchemy import select, text

from app.db import Base, make_engine, make_session_factory
from app.models import Session as UserSession, User


def test_foreign_keys_pragma_is_enabled():
    engine = make_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA foreign_keys")).scalar()
    assert result == 1


def test_user_delete_cascades_to_sessions():
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)

    with factory() as s:
        u = User(username="alice", password_hash="x")
        s.add(u)
        s.commit()
        s.add(UserSession(token="t" * 50, user_id=u.id, expires_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc)))
        s.commit()
        uid = u.id

    with factory() as s:
        u = s.get(User, uid)
        s.delete(u)
        s.commit()

    with factory() as s:
        assert s.scalars(select(UserSession).where(UserSession.user_id == uid)).first() is None
