from datetime import datetime, timezone
from app.db import Base, make_engine, make_session_factory
from app.models import MediaItem, Session as UserSession, User, WatchProgress


def setup_db():
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def test_user_columns_and_defaults():
    factory = setup_db()
    with factory() as s:
        u = User(username="alice", password_hash="x", is_admin=False)
        s.add(u)
        s.commit()
        s.refresh(u)
        assert u.id is not None
        assert u.must_change_password is True
        assert isinstance(u.created_at, datetime)


def test_session_links_to_user():
    factory = setup_db()
    with factory() as s:
        u = User(username="bob", password_hash="x")
        s.add(u)
        s.commit()
        sess = UserSession(token="t" * 43, user_id=u.id, expires_at=datetime.now(timezone.utc))
        s.add(sess)
        s.commit()
        assert sess.user_id == u.id


def test_media_item_and_watch_progress_models_exist():
    # Эти таблицы нужны в схеме сразу (для будущих планов), но в Плане 1 не используются.
    factory = setup_db()
    with factory() as s:
        u = User(username="dave", password_hash="x")
        s.add(u)
        s.commit()
        m = MediaItem(torrent_hash="abc", title="T", file_path="/x", size_bytes=1, added_by=u.id)
        s.add(m)
        s.commit()
        w = WatchProgress(user_id=u.id, media_id=m.id, position_seconds=0)
        s.add(w)
        s.commit()
        assert m.id is not None
        assert w.position_seconds == 0
