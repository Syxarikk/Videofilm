from app.auth.passwords import hash_password, verify_password


def test_hash_then_verify_succeeds():
    h = hash_password("correcthorsebatterystaple")
    assert verify_password("correcthorsebatterystaple", h) is True


def test_verify_with_wrong_password_fails():
    h = hash_password("correct")
    assert verify_password("wrong", h) is False


def test_hash_is_not_plaintext():
    h = hash_password("secret123456")
    assert "secret123456" not in h
    assert h.startswith("$2b$")  # bcrypt prefix


def test_verify_rejects_corrupt_hash():
    assert verify_password("anything", "not-a-hash") is False
