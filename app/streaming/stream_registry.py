"""In-memory tracker активных HLS-стримов.

Ключ — пара (media_id, user_id), потому что один media может одновременно смотреть несколько юзеров.
Значение — StreamHandle с подпроцессом ffmpeg и временной директорией для сегментов.

Идея: при каждом запросе сегмента/плейлиста — touch().
Watchdog периодически вызывает idle_streams() и убивает старьё.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class StreamHandle:
    media_id: int
    user_id: int
    work_dir: str
    process: object  # subprocess.Popen (или None в тестах) — типизация subprocess неудобна
    seek_seconds: float = 0.0
    last_access: float = field(default_factory=time.time)


class StreamRegistry:
    def __init__(self):
        self._streams: dict[tuple[int, int], StreamHandle] = {}
        self._lock = threading.Lock()

    def register(self, handle: StreamHandle) -> None:
        with self._lock:
            self._streams[(handle.media_id, handle.user_id)] = handle

    def get(self, media_id: int, user_id: int) -> StreamHandle | None:
        with self._lock:
            return self._streams.get((media_id, user_id))

    def unregister(self, media_id: int, user_id: int) -> StreamHandle | None:
        with self._lock:
            return self._streams.pop((media_id, user_id), None)

    def touch(self, media_id: int, user_id: int) -> None:
        with self._lock:
            h = self._streams.get((media_id, user_id))
            if h is not None:
                h.last_access = time.time()

    def idle_streams(self, idle_seconds: float) -> Iterable[StreamHandle]:
        cutoff = time.time() - idle_seconds
        with self._lock:
            return [h for h in self._streams.values() if h.last_access < cutoff]

    def all_streams(self) -> Iterable[StreamHandle]:
        with self._lock:
            return list(self._streams.values())


# Глобальный инстанс — синглтон на процесс
_registry: StreamRegistry | None = None


def get_registry() -> StreamRegistry:
    global _registry
    if _registry is None:
        _registry = StreamRegistry()
    return _registry
