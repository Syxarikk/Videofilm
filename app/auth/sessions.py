import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DbSession

from app.models import Session as UserSession


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_session(session: DbSession, *, user_id: int, ttl_days: int, is_partial: bool) -> str:
    token = secrets.token_urlsafe(48)
    obj = UserSession(
        token=token,
        user_id=user_id,
        expires_at=_now() + timedelta(days=ttl_days),
        is_partial=is_partial,
    )
    session.add(obj)
    return token


def get_active_session(session: DbSession, token: str) -> UserSession | None:
    if not token:
        return None
    stmt = select(UserSession).where(UserSession.token == token, UserSession.expires_at > _now())
    sess = session.scalars(stmt).first()
    if sess is not None and sess.expires_at.tzinfo is None:
        sess.expires_at = sess.expires_at.replace(tzinfo=timezone.utc)
    return sess


def delete_session(session: DbSession, token: str) -> None:
    session.execute(delete(UserSession).where(UserSession.token == token))


def promote_session(session: DbSession, token: str, *, ttl_days: int) -> None:
    sess = session.scalars(select(UserSession).where(UserSession.token == token)).first()
    if sess is None:
        return
    sess.is_partial = False
    sess.expires_at = _now() + timedelta(days=ttl_days)
    session.flush()
