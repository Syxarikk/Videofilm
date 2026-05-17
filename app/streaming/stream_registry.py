"""In-memory tracker активных HLS-стримов.

Ключ — пара (target_id, user_id). target_id — строка:
  "m:42"  — для MediaItem id 42 (фильм)
  "e:128" — для Episode id 128

Один media может одновременно смотреть несколько юзеров.
Watchdog периодически вызывает idle_streams() и убивает старьё.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Iterable


def media_key(media_id: int) -> str:
    return f"m:{media_id}"


def episode_key(episode_id: int) -> str:
    return f"e:{episode_id}"


@dataclass
class StreamHandle:
    target_id: str
    user_id: int
    work_dir: str
    process: object  # subprocess.Popen или None в тестах
    seek_seconds: float = 0.0
    last_access: float = field(default_factory=time.time)


class StreamRegistry:
    def __init__(self):
        self._streams: dict[tuple[str, int], StreamHandle] = {}
        self._lock = threading.Lock()

    def register(self, handle: StreamHandle) -> None:
        with self._lock:
            self._streams[(handle.target_id, handle.user_id)] = handle

    def get(self, target_id: str, user_id: int) -> StreamHandle | None:
        with self._lock:
            return self._streams.get((target_id, user_id))

    def unregister(self, target_id: str, user_id: int) -> StreamHandle | None:
        with self._lock:
            return self._streams.pop((target_id, user_id), None)

    def touch(self, target_id: str, user_id: int) -> None:
        with self._lock:
            h = self._streams.get((target_id, user_id))
            if h is not None:
                h.last_access = time.time()

    def idle_streams(self, idle_seconds: float) -> Iterable[StreamHandle]:
        cutoff = time.time() - idle_seconds
        with self._lock:
            return [h for h in self._streams.values() if h.last_access < cutoff]

    def all_streams(self) -> Iterable[StreamHandle]:
        with self._lock:
            return list(self._streams.values())


_registry: StreamRegistry | None = None


def get_registry() -> StreamRegistry:
    global _registry
    if _registry is None:
        _registry = StreamRegistry()
    return _registry
