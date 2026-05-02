from sqlalchemy import select

from app.db import Base, make_engine, make_session_factory
from app.models import User
from scripts.create_admin import create_admin


def test_create_admin_creates_user_with_admin_flag():
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    with factory() as s:
        create_admin(s, username="root", password="bootstrap-password-1")
        s.commit()
    with factory() as s:
        u = s.scalars(select(User).where(User.username == "root")).one()
        assert u.is_admin is True
        assert u.must_change_password is True


def test_create_admin_rejects_short_password():
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    import pytest
    with factory() as s:
        with pytest.raises(ValueError):
            create_admin(s, username="root", password="short")


def test_create_admin_rejects_duplicate_username():
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    with factory() as s:
        create_admin(s, username="root", password="bootstrap-password-1")
        s.commit()
    import pytest
    with factory() as s:
        with pytest.raises(ValueError):
            create_admin(s, username="root", password="bootstrap-password-2")
