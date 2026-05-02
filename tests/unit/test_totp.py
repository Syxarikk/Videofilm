import pyotp
from freezegun import freeze_time

from app.auth.totp import (
    decrypt_secret,
    encrypt_secret,
    generate_secret,
    provisioning_uri,
    qr_png_bytes,
    verify_code,
)


def test_generate_secret_returns_base32_string():
    s = generate_secret()
    assert len(s) >= 16
    assert s.isalnum()


def test_provisioning_uri_contains_issuer_and_username():
    secret = generate_secret()
    uri = provisioning_uri(secret, "alice", "TestSrv")
    assert uri.startswith("otpauth://totp/")
    assert "alice" in uri
    assert "TestSrv" in uri


def test_qr_png_returns_bytes_starting_with_png_magic():
    uri = provisioning_uri(generate_secret(), "alice", "TestSrv")
    data = qr_png_bytes(uri)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


@freeze_time("2026-05-02 12:00:00")
def test_verify_code_accepts_current_code():
    secret = generate_secret()
    code = pyotp.TOTP(secret).now()
    assert verify_code(secret, code) is True


@freeze_time("2026-05-02 12:00:00")
def test_verify_code_rejects_wrong_code():
    secret = generate_secret()
    assert verify_code(secret, "000000") is False


def test_encrypt_then_decrypt_roundtrip():
    key = b"k" * 32
    secret = "JBSWY3DPEHPK3PXP"
    enc = encrypt_secret(secret, key)
    assert enc != secret
    assert decrypt_secret(enc, key) == secret


def test_decrypt_with_wrong_key_raises():
    enc = encrypt_secret("JBSWY3DPEHPK3PXP", b"a" * 32)
    import pytest
    with pytest.raises(Exception):
        decrypt_secret(enc, b"b" * 32)
