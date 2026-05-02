import pytest

from app.csrf import generate_token, verify_token


def test_generate_returns_two_unique_tokens():
    a = generate_token("session-key")
    b = generate_token("session-key")
    assert a != b


def test_verify_accepts_token_for_same_secret():
    secret = "session-key"
    t = generate_token(secret)
    assert verify_token(t, secret) is True


def test_verify_rejects_token_for_different_secret():
    t = generate_token("alice-session")
    assert verify_token(t, "bob-session") is False


def test_verify_rejects_garbage():
    assert verify_token("not-a-token", "any") is False
