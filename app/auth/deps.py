from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth.sessions import get_active_session
from app.deps import get_db
from app.models import User

SESSION_COOKIE = "session"


def _resolve_current_user_or_none(db: Session, token: str | None, *, allow_partial: bool) -> User | None:
    if not token:
        return None
    sess = get_active_session(db, token)
    if sess is None:
        return None
    if sess.is_partial and not allow_partial:
        return None
    return db.get(User, sess.user_id)


def _resolve_current_user(db: Session, token: str | None, *, allow_partial: bool) -> User:
    user = _resolve_current_user_or_none(db, token, allow_partial=allow_partial)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return user


def get_current_user(request: Request, db: Annotated[Session, Depends(get_db)]) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    return _resolve_current_user(db, token, allow_partial=False)


def get_current_user_partial(request: Request, db: Annotated[Session, Depends(get_db)]) -> User:
    """Allows partial sessions (used during the must-change-password flow)."""
    token = request.cookies.get(SESSION_COOKIE)
    return _resolve_current_user(db, token, allow_partial=True)


def get_current_user_optional(request: Request, db: Annotated[Session, Depends(get_db)]) -> User | None:
    token = request.cookies.get(SESSION_COOKIE)
    return _resolve_current_user_or_none(db, token, allow_partial=False)


def require_admin(user: Annotated[User, Depends(get_current_user)]) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user
