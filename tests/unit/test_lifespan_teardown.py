import asyncio
import tempfile
from unittest.mock import MagicMock

import pytest

from app.streaming.stream_registry import StreamHandle, get_registry
from app.streaming.watchdog import sweep_idle


def test_sweep_idle_with_zero_threshold_kills_all():
    """sweep_idle(reg, idle_seconds=0) убивает все стримы — для shutdown'а."""
    reg = get_registry()
    # Очистим registry перед тестом
    for h in list(reg.all_streams()):
        reg.unregister(h.media_id, h.user_id)

    work_dir = tempfile.mkdtemp(prefix="lifespan_test_")
    proc = MagicMock(); proc.poll.return_value = None
    reg.register(StreamHandle(media_id=42, user_id=1, work_dir=work_dir, process=proc))

    killed = sweep_idle(reg, idle_seconds=0.0)
    assert killed >= 1
    assert reg.get(42, 1) is None
    # На Windows kill() звонит send_signal вместо terminate; принимаем любой
    assert proc.terminate.called or proc.send_signal.called
