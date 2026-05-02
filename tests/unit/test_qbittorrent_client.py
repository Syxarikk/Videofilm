import httpx
import pytest
import respx

from app.torrents.client import QBittorrentClient, QBittorrentError
from app.torrents.types import TorrentInfo


@respx.mock
def test_login_succeeds_and_caches_cookie():
    respx.post("http://qb/api/v2/auth/login").mock(
        return_value=httpx.Response(200, text="Ok.", headers={"set-cookie": "SID=abc; path=/"})
    )
    c = QBittorrentClient("http://qb", "admin", "secret")
    c.login()
    # Повторный login — без новых HTTP-вызовов
    assert respx.calls.call_count == 1


@respx.mock
def test_login_wrong_credentials_raises():
    respx.post("http://qb/api/v2/auth/login").mock(
        return_value=httpx.Response(200, text="Fails.")
    )
    c = QBittorrentClient("http://qb", "admin", "wrong")
    with pytest.raises(QBittorrentError):
        c.login()


@respx.mock
def test_add_magnet_calls_correct_endpoint():
    respx.post("http://qb/api/v2/auth/login").mock(return_value=httpx.Response(200, text="Ok."))
    add_route = respx.post("http://qb/api/v2/torrents/add").mock(return_value=httpx.Response(200))
    c = QBittorrentClient("http://qb", "admin", "secret")
    c.add_magnet("magnet:?xt=urn:btih:abc", save_path="/srv/Общее/downloads")
    assert add_route.called
    sent = add_route.calls.last.request
    body = sent.content.decode()
    assert "magnet:?xt=urn:btih:abc" in body
    assert "/srv/Общее/downloads" in body


@respx.mock
def test_list_torrents_returns_typed_objects():
    respx.post("http://qb/api/v2/auth/login").mock(return_value=httpx.Response(200, text="Ok."))
    respx.get("http://qb/api/v2/torrents/info").mock(return_value=httpx.Response(200, json=[
        {
            "hash": "abc123",
            "name": "Some.Movie.2024.1080p.mkv",
            "progress": 0.42,
            "dlspeed": 1500000,
            "state": "downloading",
            "size": 4_000_000_000,
            "save_path": "/srv/Общее/downloads",
            "content_path": "/srv/Общее/downloads/Some.Movie.2024.1080p.mkv",
            "eta": 600,
        }
    ]))
    c = QBittorrentClient("http://qb", "admin", "secret")
    torrents = c.list_torrents()
    assert len(torrents) == 1
    t = torrents[0]
    assert isinstance(t, TorrentInfo)
    assert t.hash == "abc123"
    assert t.progress == 0.42
    assert t.state == "downloading"
    assert t.is_complete is False


@respx.mock
def test_list_torrents_marks_completed_state():
    respx.post("http://qb/api/v2/auth/login").mock(return_value=httpx.Response(200, text="Ok."))
    respx.get("http://qb/api/v2/torrents/info").mock(return_value=httpx.Response(200, json=[
        {"hash": "h", "name": "n", "progress": 1.0, "dlspeed": 0, "state": "uploading",
         "size": 1, "save_path": "/x", "content_path": "/x/n", "eta": 0}
    ]))
    c = QBittorrentClient("http://qb", "admin", "secret")
    [t] = c.list_torrents()
    assert t.is_complete is True


@respx.mock
def test_delete_torrent_with_files():
    respx.post("http://qb/api/v2/auth/login").mock(return_value=httpx.Response(200, text="Ok."))
    delete_route = respx.post("http://qb/api/v2/torrents/delete").mock(return_value=httpx.Response(200))
    c = QBittorrentClient("http://qb", "admin", "secret")
    c.delete_torrent("abc123", delete_files=True)
    assert delete_route.called
    body = delete_route.calls.last.request.content.decode()
    assert "abc123" in body
    assert "deleteFiles=true" in body


@respx.mock
def test_request_failure_raises_qbittorrent_error():
    respx.post("http://qb/api/v2/auth/login").mock(return_value=httpx.Response(200, text="Ok."))
    respx.get("http://qb/api/v2/torrents/info").mock(return_value=httpx.Response(500))
    c = QBittorrentClient("http://qb", "admin", "secret")
    with pytest.raises(QBittorrentError):
        c.list_torrents()
