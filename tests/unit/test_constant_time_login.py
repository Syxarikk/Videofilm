import time

import pytest

from app.auth.passwords import verify_password
from app.auth.routes import _DUMMY_HASH


def test_dummy_hash_is_a_real_bcrypt_hash():
    """Sanity: dummy-хеш можно проверить через verify_password (не падает)."""
    assert verify_password("anything", _DUMMY_HASH) is False


def test_login_timing_with_unknown_user_uses_bcrypt(client, db_factory, csrf_for):
    """Unknown vs known user login times must be similar (both run bcrypt)."""
    from app.auth.passwords import hash_password
    from app.models import User
    with db_factory() as s:
        s.add(User(username="alice", password_hash=hash_password("correct-password-12"),
                   must_change_password=False, totp_enabled=True, totp_secret_encrypted="x"))
        s.commit()

    # Unknown user
    t1 = time.monotonic()
    client.post("/login", data={"username": "ghost", "password": "wrong", "csrf_token": csrf_for(None)})
    dt_unknown = time.monotonic() - t1

    # Known user, wrong password
    t2 = time.monotonic()
    client.post("/login", data={"username": "alice", "password": "wrong", "csrf_token": csrf_for(None)})
    dt_known = time.monotonic() - t2

    # Различие должно быть < 100ms (bcrypt cost=12 ~250ms; в идеале они равны).
    # Допускаем шум JIT/планировщика.
    assert abs(dt_unknown - dt_known) < 0.15, f"unknown={dt_unknown:.3f}s, known={dt_known:.3f}s"
