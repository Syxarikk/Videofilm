import os
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

from app.streaming.stream_registry import StreamHandle, StreamRegistry
from app.streaming.watchdog import sweep_idle
from app.streaming import watchdog


def test_idle_threshold_seconds_is_300():
    # Защита от регрессии: порог watchdog должен быть 300с, чтобы
    # переживать паузу + временную потерю heartbeat.
    assert watchdog.IDLE_THRESHOLD_SECONDS == 300.0


def _assert_process_killed(proc):
    # На Windows ffmpeg_runner.kill() шлёт CTRL_BREAK_EVENT через send_signal,
    # на POSIX — proc.terminate(). Проверяем оба варианта.
    if os.name == "nt":
        assert proc.send_signal.called, "ожидали send_signal(CTRL_BREAK_EVENT) на Windows"
    else:
        proc.terminate.assert_called()


def _assert_process_not_killed(proc):
    if os.name == "nt":
        proc.send_signal.assert_not_called()
    else:
        proc.terminate.assert_not_called()


def test_sweep_idle_kills_old_streams_and_unregisters():
    reg = StreamRegistry()
    work_dir = tempfile.mkdtemp(prefix="watchdog_test_")
    proc = MagicMock()
    proc.poll.return_value = None  # «жив»
    handle = StreamHandle(media_id=1, user_id=2, work_dir=work_dir, process=proc)
    reg.register(handle)
    object.__setattr__(handle, "last_access", time.time() - 120)

    sweep_idle(reg, idle_seconds=60)

    assert reg.get(1, 2) is None
    _assert_process_killed(proc)  # процесс убит
    assert not Path(work_dir).exists()  # папка удалена


def test_sweep_idle_skips_active_streams(tmp_path):
    reg = StreamRegistry()
    proc = MagicMock(); proc.poll.return_value = None
    handle = StreamHandle(media_id=1, user_id=2, work_dir=str(tmp_path), process=proc)
    reg.register(handle)
    sweep_idle(reg, idle_seconds=60)
    assert reg.get(1, 2) is handle
    _assert_process_not_killed(proc)
