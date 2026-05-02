import hashlib
import secrets
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BackupCode

_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"  # без 0/O/1/l/I — меньше шанса опечаток


def _random_block(n: int = 5) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(n))


def generate_codes(count: int = 10) -> list[str]:
    seen: set[str] = set()
    codes: list[str] = []
    while len(codes) < count:
        c = f"{_random_block()}-{_random_block()}"
        if c in seen:
            continue
        seen.add(c)
        codes.append(c)
    return codes


def hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def verify_and_consume(session: Session, user_id: int, code: str) -> bool:
    h = hash_code(code)
    stmt = select(BackupCode).where(
        BackupCode.user_id == user_id,
        BackupCode.code_hash == h,
        BackupCode.used_at.is_(None),
    )
    row = session.scalars(stmt).first()
    if row is None:
        return False
    row.used_at = datetime.now(timezone.utc)
    session.flush()
    return True
