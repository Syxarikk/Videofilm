from datetime import datetime, timedelta, timezone

from freezegun import freeze_time

from app.auth.sessions import (
    create_session,
    delete_session,
    get_active_session,
    promote_session,
)
from app.db import Base, make_engine, make_session_factory
from app.models import User


def setup():
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    with factory() as s:
        u = User(username="alice", password_hash="x")
        s.add(u)
        s.commit()
        s.refresh(u)
        return factory, u.id


@freeze_time("2026-05-02 12:00:00")
def test_create_session_returns_token_and_persists():
    factory, uid = setup()
    with factory() as s:
        token = create_session(s, user_id=uid, ttl_days=30, is_partial=False)
        s.commit()
    assert len(token) >= 32
    with factory() as s:
        sess = get_active_session(s, token)
        assert sess is not None
        assert sess.user_id == uid
        assert sess.is_partial is False


@freeze_time("2026-05-02 12:00:00")
def test_get_active_session_rejects_expired():
    factory, uid = setup()
    with factory() as s:
        token = create_session(s, user_id=uid, ttl_days=1, is_partial=False)
        s.commit()
    with freeze_time("2026-06-01"):
        with factory() as s:
            assert get_active_session(s, token) is None


def test_get_active_session_unknown_token():
    factory, _ = setup()
    with factory() as s:
        assert get_active_session(s, "not-a-real-token") is None


def test_delete_session_removes_it():
    factory, uid = setup()
    with factory() as s:
        token = create_session(s, user_id=uid, ttl_days=1, is_partial=False)
        s.commit()
    with factory() as s:
        delete_session(s, token)
        s.commit()
    with factory() as s:
        assert get_active_session(s, token) is None


def test_promote_session_clears_partial_flag_and_extends_ttl():
    factory, uid = setup()
    with freeze_time("2026-05-02 12:00:00"):
        with factory() as s:
            token = create_session(s, user_id=uid, ttl_days=1, is_partial=True)
            s.commit()
    with freeze_time("2026-05-02 12:00:30"):
        with factory() as s:
            promote_session(s, token, ttl_days=30)
            s.commit()
        with factory() as s:
            sess = get_active_session(s, token)
            assert sess.is_partial is False
            # expires near 30 days later
            assert sess.expires_at > datetime(2026, 5, 30, tzinfo=timezone.utc)
