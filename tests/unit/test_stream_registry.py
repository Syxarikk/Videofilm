import time

import pytest

from app.streaming.stream_registry import StreamHandle, StreamRegistry, episode_key, media_key


def test_register_and_lookup():
    reg = StreamRegistry()
    h = StreamHandle(target_id="m:1", user_id=2, work_dir="/tmp/x", process=None)
    reg.register(h)
    assert reg.get("m:1", 2) is h


def test_register_replaces_existing():
    reg = StreamRegistry()
    h1 = StreamHandle(target_id="m:1", user_id=2, work_dir="/tmp/x1", process=None)
    h2 = StreamHandle(target_id="m:1", user_id=2, work_dir="/tmp/x2", process=None)
    reg.register(h1)
    reg.register(h2)
    assert reg.get("m:1", 2) is h2


def test_unregister():
    reg = StreamRegistry()
    h = StreamHandle(target_id="m:1", user_id=2, work_dir="/tmp/x", process=None)
    reg.register(h)
    reg.unregister("m:1", 2)
    assert reg.get("m:1", 2) is None


def test_touch_updates_last_access():
    reg = StreamRegistry()
    h = StreamHandle(target_id="m:1", user_id=2, work_dir="/tmp/x", process=None)
    reg.register(h)
    t1 = h.last_access
    time.sleep(0.01)
    reg.touch("m:1", 2)
    assert reg.get("m:1", 2).last_access > t1


def test_idle_streams_returns_those_past_threshold():
    reg = StreamRegistry()
    h = StreamHandle(target_id="m:1", user_id=2, work_dir="/tmp/x", process=None)
    reg.register(h)
    object.__setattr__(h, "last_access", time.time() - 120)
    idle = list(reg.idle_streams(idle_seconds=60))
    assert len(idle) == 1
    assert idle[0].target_id == "m:1"


def test_media_key_and_episode_key_helpers():
    assert media_key(42) == "m:42"
    assert episode_key(128) == "e:128"


def test_movies_and_episodes_use_distinct_keys():
    reg = StreamRegistry()
    m = StreamHandle(target_id=media_key(42), user_id=1, work_dir="/tmp/m", process=None)
    e = StreamHandle(target_id=episode_key(42), user_id=1, work_dir="/tmp/e", process=None)
    reg.register(m); reg.register(e)
    assert reg.get(media_key(42), 1) is m
    assert reg.get(episode_key(42), 1) is e
