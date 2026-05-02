import time

import pytest

from app.streaming.stream_registry import StreamHandle, StreamRegistry


def test_register_and_lookup():
    reg = StreamRegistry()
    h = StreamHandle(media_id=1, user_id=2, work_dir="/tmp/x", process=None)
    reg.register(h)
    assert reg.get(1, 2) is h


def test_register_replaces_existing():
    reg = StreamRegistry()
    h1 = StreamHandle(media_id=1, user_id=2, work_dir="/tmp/x1", process=None)
    h2 = StreamHandle(media_id=1, user_id=2, work_dir="/tmp/x2", process=None)
    reg.register(h1)
    reg.register(h2)
    assert reg.get(1, 2) is h2


def test_unregister():
    reg = StreamRegistry()
    h = StreamHandle(media_id=1, user_id=2, work_dir="/tmp/x", process=None)
    reg.register(h)
    reg.unregister(1, 2)
    assert reg.get(1, 2) is None


def test_touch_updates_last_access():
    reg = StreamRegistry()
    h = StreamHandle(media_id=1, user_id=2, work_dir="/tmp/x", process=None)
    reg.register(h)
    t1 = h.last_access
    time.sleep(0.01)
    reg.touch(1, 2)
    assert reg.get(1, 2).last_access > t1


def test_idle_streams_returns_those_past_threshold():
    reg = StreamRegistry()
    h = StreamHandle(media_id=1, user_id=2, work_dir="/tmp/x", process=None)
    reg.register(h)
    # Подменяем время доступа на «давно»
    object.__setattr__(h, "last_access", time.time() - 120)
    idle = list(reg.idle_streams(idle_seconds=60))
    assert len(idle) == 1
    assert idle[0].media_id == 1
