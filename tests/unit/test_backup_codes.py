from app.auth.backup_codes import (
    generate_codes,
    hash_code,
    verify_and_consume,
)
from app.db import Base, make_engine, make_session_factory
from app.models import BackupCode, User


def setup_db_with_user():
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    with factory() as s:
        u = User(username="alice", password_hash="x")
        s.add(u)
        s.commit()
        s.refresh(u)
        return factory, u.id


def test_generate_codes_returns_10_unique_human_readable():
    codes = generate_codes()
    assert len(codes) == 10
    assert len(set(codes)) == 10
    for c in codes:
        assert len(c) == 11        # 5-5 with dash, e.g. "ab3cd-ef9gh"
        assert c[5] == "-"


def test_hash_code_is_deterministic_and_not_plaintext():
    h1 = hash_code("abcde-fghij")
    h2 = hash_code("abcde-fghij")
    assert h1 == h2
    assert "abcde-fghij" not in h1


def test_verify_and_consume_marks_code_used_and_only_once():
    factory, uid = setup_db_with_user()
    codes = generate_codes()
    with factory() as s:
        for c in codes:
            s.add(BackupCode(user_id=uid, code_hash=hash_code(c)))
        s.commit()

    with factory() as s:
        assert verify_and_consume(s, uid, codes[0]) is True
        s.commit()

    with factory() as s:
        # Второй раз тот же код — нет.
        assert verify_and_consume(s, uid, codes[0]) is False


def test_verify_unknown_code_returns_false():
    factory, uid = setup_db_with_user()
    with factory() as s:
        assert verify_and_consume(s, uid, "wrong-codez") is False
