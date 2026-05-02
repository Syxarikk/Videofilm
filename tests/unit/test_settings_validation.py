from app.config import Settings


def test_settings_has_hls_work_root_with_default(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "x" * 64)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("MEDIA_ROOT", "/tmp/media")
    monkeypatch.setenv("QBITTORRENT_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("QBITTORRENT_USERNAME", "admin")
    monkeypatch.setenv("QBITTORRENT_PASSWORD", "secret")
    monkeypatch.setenv("TOTP_ISSUER", "TestSrv")
    monkeypatch.delenv("HLS_WORK_ROOT", raising=False)
    s = Settings()
    # Default: системная temp-папка (для dev на Windows / Mac тоже работает)
    import tempfile
    assert s.hls_work_root == tempfile.gettempdir()


def test_settings_hls_work_root_overridable(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "x" * 64)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("MEDIA_ROOT", "/tmp/media")
    monkeypatch.setenv("QBITTORRENT_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("QBITTORRENT_USERNAME", "admin")
    monkeypatch.setenv("QBITTORRENT_PASSWORD", "secret")
    monkeypatch.setenv("TOTP_ISSUER", "TestSrv")
    monkeypatch.setenv("HLS_WORK_ROOT", "/var/lib/mediasrv/hls")
    s = Settings()
    assert s.hls_work_root == "/var/lib/mediasrv/hls"


def test_media_root_must_be_absolute(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "x" * 64)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("MEDIA_ROOT", "relative/path")  # не абсолютный
    monkeypatch.setenv("QBITTORRENT_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("QBITTORRENT_USERNAME", "admin")
    monkeypatch.setenv("QBITTORRENT_PASSWORD", "secret")
    monkeypatch.setenv("TOTP_ISSUER", "TestSrv")
    import pytest
    with pytest.raises(ValueError):
        Settings()


def test_media_root_absolute_path_accepted(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "x" * 64)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("MEDIA_ROOT", "/srv/Общее")
    monkeypatch.setenv("QBITTORRENT_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("QBITTORRENT_USERNAME", "admin")
    monkeypatch.setenv("QBITTORRENT_PASSWORD", "secret")
    monkeypatch.setenv("TOTP_ISSUER", "TestSrv")
    s = Settings()
    assert s.media_root == "/srv/Общее"


def test_media_root_windows_absolute_path_accepted(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "x" * 64)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("MEDIA_ROOT", "C:\\Users\\Test\\media")
    monkeypatch.setenv("QBITTORRENT_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("QBITTORRENT_USERNAME", "admin")
    monkeypatch.setenv("QBITTORRENT_PASSWORD", "secret")
    monkeypatch.setenv("TOTP_ISSUER", "TestSrv")
    s = Settings()
    assert s.media_root == "C:\\Users\\Test\\media"
