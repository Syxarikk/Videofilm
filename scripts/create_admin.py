"""
Bootstrap-скрипт: создать первого админа.

Использование:
    python -m scripts.create_admin
(скрипт в интерактивном режиме спросит логин и пароль)

или для CI/install.sh:
    python -m scripts.create_admin --username root --password 'bootstrap-password-1'
"""
import argparse
import getpass
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.passwords import hash_password
from app.config import get_settings
from app.db import make_engine, make_session_factory
from app.models import User

MIN_PASSWORD_LEN = 12


def create_admin(session: Session, *, username: str, password: str) -> User:
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"Пароль должен быть не короче {MIN_PASSWORD_LEN} символов")
    if session.scalars(select(User).where(User.username == username)).first():
        raise ValueError(f"Пользователь {username!r} уже существует")
    u = User(
        username=username,
        password_hash=hash_password(password),
        must_change_password=True,
        totp_enabled=False,
        is_admin=True,
    )
    session.add(u)
    session.flush()
    return u


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", help="Логин админа")
    parser.add_argument("--password", help="Временный пароль админа")
    args = parser.parse_args()

    username = args.username or input("Логин: ").strip()
    password = args.password or getpass.getpass("Временный пароль: ")

    settings = get_settings()
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)
    with factory() as session:
        try:
            u = create_admin(session, username=username, password=password)
            session.commit()
        except ValueError as e:
            print(f"Ошибка: {e}", file=sys.stderr)
            return 1
    print(f"Создан админ {u.username!r} (id={u.id}). При первом входе ему предложат сменить пароль и активировать 2FA.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
