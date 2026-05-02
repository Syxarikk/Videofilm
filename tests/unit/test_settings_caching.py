from app.config import get_settings
from app.deps import get_db_factory


def test_get_settings_returns_same_instance():
    a = get_settings()
    b = get_settings()
    assert a is b


def test_get_db_factory_returns_same_instance():
    f1 = get_db_factory()
    f2 = get_db_factory()
    assert f1 is f2
