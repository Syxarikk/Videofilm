import base64
import io
import os
from hashlib import sha256
from urllib.parse import quote

import pyotp
import qrcode
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def generate_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, username: str, issuer: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=quote(username), issuer_name=quote(issuer))


def qr_png_bytes(uri: str) -> bytes:
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def verify_code(secret: str, code: str, window: int = 1) -> bool:
    return pyotp.TOTP(secret).verify(code, valid_window=window)


def _derive_key(session_secret: str | bytes) -> bytes:
    raw = session_secret if isinstance(session_secret, bytes) else session_secret.encode("utf-8")
    return sha256(b"totp-key:" + raw).digest()


def encrypt_secret(secret: str, key: bytes) -> str:
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, secret.encode("utf-8"), None)
    return base64.b64encode(nonce + ct).decode("ascii")


def decrypt_secret(encrypted_b64: str, key: bytes) -> str:
    raw = base64.b64decode(encrypted_b64)
    nonce, ct = raw[:12], raw[12:]
    return AESGCM(key).decrypt(nonce, ct, None).decode("utf-8")
