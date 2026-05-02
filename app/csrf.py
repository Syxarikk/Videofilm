import hmac
import secrets
from hashlib import sha256


def _sign(value: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), value.encode("utf-8"), sha256).hexdigest()


def generate_token(session_key: str) -> str:
    nonce = secrets.token_urlsafe(16)
    sig = _sign(nonce, session_key)
    return f"{nonce}.{sig}"


def verify_token(token: str, session_key: str) -> bool:
    if not token or "." not in token:
        return False
    try:
        nonce, sig = token.rsplit(".", 1)
    except ValueError:
        return False
    expected = _sign(nonce, session_key)
    return hmac.compare_digest(sig, expected)
