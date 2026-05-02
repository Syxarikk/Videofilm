import tempfile as _tempfile
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    session_secret: str
    database_url: str
    media_root: str
    qbittorrent_url: str
    qbittorrent_username: str
    qbittorrent_password: str
    totp_issuer: str = "MediaServer"
    hls_work_root: str = _tempfile.gettempdir()

    @field_validator("session_secret")
    @classmethod
    def session_secret_long_enough(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("SESSION_SECRET must be at least 32 characters")
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
