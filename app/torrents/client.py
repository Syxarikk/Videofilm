"""Тонкая обёртка над qBittorrent Web UI HTTP API.

Документация: https://github.com/qbittorrent/qBittorrent/wiki/WebUI-API-(qBittorrent-4.1)
"""
from typing import Any

import httpx

from app.torrents.types import TorrentInfo


class QBittorrentError(Exception):
    """Любая ошибка взаимодействия с qBittorrent — сеть, аутентификация, 5xx, неверный формат."""


class QBittorrentClient:
    def __init__(self, base_url: str, username: str, password: str, timeout: float = 10.0):
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._client = httpx.Client(base_url=self._base_url, timeout=timeout)
        self._logged_in = False

    def login(self) -> None:
        if self._logged_in:
            return
        try:
            r = self._client.post(
                "/api/v2/auth/login",
                data={"username": self._username, "password": self._password},
            )
        except httpx.HTTPError as e:
            raise QBittorrentError(f"login: connection failed: {e}") from e
        if r.status_code != 200 or "Ok." not in r.text:
            raise QBittorrentError(f"login: rejected (status={r.status_code}, body={r.text!r})")
        self._logged_in = True

    def add_magnet(self, magnet: str, *, save_path: str) -> None:
        self.login()
        # qBittorrent /torrents/add expects multipart/form-data; httpx encodes
        # multipart fields as raw UTF-8 (no percent-encoding), which keeps the
        # magnet URI and save_path readable in the request body.
        try:
            r = self._client.post(
                "/api/v2/torrents/add",
                files={
                    "urls": (None, magnet),
                    "savepath": (None, save_path),
                    "autoTMM": (None, "false"),
                },
            )
        except httpx.HTTPError as e:
            raise QBittorrentError(f"add_magnet: connection failed: {e}") from e
        if r.status_code != 200:
            raise QBittorrentError(f"add_magnet: status={r.status_code}, body={r.text!r}")

    def list_torrents(self) -> list[TorrentInfo]:
        self.login()
        try:
            r = self._client.get("/api/v2/torrents/info")
        except httpx.HTTPError as e:
            raise QBittorrentError(f"list_torrents: connection failed: {e}") from e
        if r.status_code != 200:
            raise QBittorrentError(f"list_torrents: status={r.status_code}")
        try:
            payload: list[dict[str, Any]] = r.json()
        except ValueError as e:
            raise QBittorrentError(f"list_torrents: invalid JSON: {e}") from e
        return [self._parse_torrent(t) for t in payload]

    def delete_torrent(self, info_hash: str, *, delete_files: bool) -> None:
        self.login()
        try:
            r = self._client.post(
                "/api/v2/torrents/delete",
                data={"hashes": info_hash, "deleteFiles": "true" if delete_files else "false"},
            )
        except httpx.HTTPError as e:
            raise QBittorrentError(f"delete_torrent: connection failed: {e}") from e
        if r.status_code != 200:
            raise QBittorrentError(f"delete_torrent: status={r.status_code}")

    def _parse_torrent(self, raw: dict[str, Any]) -> TorrentInfo:
        try:
            return TorrentInfo(
                hash=raw["hash"],
                name=raw["name"],
                progress=float(raw["progress"]),
                dlspeed=int(raw["dlspeed"]),
                state=str(raw["state"]),
                size=int(raw["size"]),
                save_path=str(raw["save_path"]),
                content_path=str(raw["content_path"]),
                eta_seconds=int(raw.get("eta", -1)),
            )
        except (KeyError, ValueError, TypeError) as e:
            raise QBittorrentError(f"unexpected torrent payload: {raw!r}") from e

    def close(self) -> None:
        self._client.close()
