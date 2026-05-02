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
