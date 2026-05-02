from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.auth.deps import _resolve_current_user, _resolve_current_user_or_none
from app.auth.sessions import create_session
from app.models import User


def test_resolve_current_user_returns_user(db_factory):
    with db_factory() as s:
        u = User(username="alice", password_hash="x")
        s.add(u)
        s.commit()
        token = create_session(s, user_id=u.id, ttl_days=1, is_partial=False)
        s.commit()
    with db_factory() as s:
        user = _resolve_current_user(s, token, allow_partial=False)
        assert user.username == "alice"


def test_resolve_rejects_partial_when_not_allowed(db_factory):
    with db_factory() as s:
        u = User(username="bob", password_hash="x")
        s.add(u)
        s.commit()
        token = create_session(s, user_id=u.id, ttl_days=1, is_partial=True)
        s.commit()
    with db_factory() as s:
        with pytest.raises(HTTPException) as exc:
            _resolve_current_user(s, token, allow_partial=False)
        assert exc.value.status_code == 401


def test_resolve_or_none_returns_none_for_missing_token(db_factory):
    with db_factory() as s:
        assert _resolve_current_user_or_none(s, None, allow_partial=False) is None
