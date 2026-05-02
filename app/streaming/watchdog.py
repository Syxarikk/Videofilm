"""Watchdog: периодически убивает ffmpeg-процессы, к которым давно не было доступа."""
import asyncio
import logging
import shutil

from app.streaming.ffmpeg_runner import kill
from app.streaming.stream_registry import StreamRegistry, get_registry

log = logging.getLogger(__name__)

IDLE_THRESHOLD_SECONDS = 60.0
SWEEP_INTERVAL_SECONDS = 15.0


def sweep_idle(reg: StreamRegistry, idle_seconds: float) -> int:
    killed = 0
    for handle in list(reg.idle_streams(idle_seconds)):
        try:
            if handle.process is not None:
                kill(handle.process)
            shutil.rmtree(handle.work_dir, ignore_errors=True)
        finally:
            reg.unregister(handle.media_id, handle.user_id)
            killed += 1
    if killed:
        log.info("watchdog: killed %d idle stream(s)", killed)
    return killed


async def watchdog_loop() -> None:
    reg = get_registry()
    while True:
        try:
            sweep_idle(reg, IDLE_THRESHOLD_SECONDS)
        except Exception:
            log.exception("watchdog iteration failed")
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
