from dataclasses import dataclass


_COMPLETE_STATES = {"uploading", "stalledUP", "queuedUP", "checkingUP", "forcedUP", "pausedUP"}


@dataclass(frozen=True, slots=True)
class TorrentInfo:
    hash: str
    name: str
    progress: float       # 0.0–1.0
    dlspeed: int          # bytes/sec
    state: str            # qBittorrent state code
    size: int             # bytes
    save_path: str        # директория сохранения
    content_path: str     # путь к контенту торрента (файл или папка)
    eta_seconds: int      # -1 если не вычислен

    @property
    def is_complete(self) -> bool:
        return self.progress >= 1.0 or self.state in _COMPLETE_STATES
