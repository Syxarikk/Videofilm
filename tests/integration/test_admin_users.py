import secrets

from app.auth.passwords import hash_password
from app.models import User


def make_admin_logged_in(client, db_factory, csrf_for):
    with db_factory() as s:
        u = User(
            username="root", password_hash=hash_password("admin-password-12"),
            must_change_password=False, is_admin=True,
        )
        s.add(u); s.commit()
    r = client.post(
        "/login",
        data={"username": "root", "password": "admin-password-12", "csrf_token": csrf_for(None)},
    )
    cookie = r.cookies.get("session")
    return cookie


def make_regular_logged_in(client, db_factory, csrf_for):
    with db_factory() as s:
        u = User(
            username="alice", password_hash=hash_password("user-password-12"),
            must_change_password=False, is_admin=False,
        )
        s.add(u); s.commit()
    r = client.post(
        "/login",
        data={"username": "alice", "password": "user-password-12", "csrf_token": csrf_for(None)},
    )
    cookie = r.cookies.get("session")
    return cookie


def test_admin_users_lists_all_users(client, db_factory, csrf_for):
    cookie = make_admin_logged_in(client, db_factory, csrf_for)
    r = client.get("/admin/users", cookies={"session": cookie})
    assert r.status_code == 200
    assert "root" in r.text


def test_regular_user_cannot_access_admin_users(client, db_factory, csrf_for):
    cookie = make_regular_logged_in(client, db_factory, csrf_for)
    r = client.get("/admin/users", cookies={"session": cookie})
    assert r.status_code == 403


def test_unauthenticated_redirected(client):
    r = client.get("/admin/users")
    assert r.status_code in (303, 401)


def test_admin_creates_user_and_sees_temp_password(client, db_factory, csrf_for):
    cookie = make_admin_logged_in(client, db_factory, csrf_for)
    r = client.post(
        "/admin/users",
        data={"username": "newbie", "csrf_token": csrf_for(cookie)},
        cookies={"session": cookie},
    )
    assert r.status_code == 200
    assert "newbie" in r.text
    assert "Временный пароль" in r.text or "temporary" in r.text.lower() or "temp_password" in r.text.lower()


def test_create_user_rejects_duplicate_username(client, db_factory, csrf_for):
    cookie = make_admin_logged_in(client, db_factory, csrf_for)
    client.post(
        "/admin/users",
        data={"username": "twin", "csrf_token": csrf_for(cookie)},
        cookies={"session": cookie},
    )
    r = client.post(
        "/admin/users",
        data={"username": "twin", "csrf_token": csrf_for(cookie)},
        cookies={"session": cookie},
    )
    assert r.status_code == 400


def test_create_user_validates_username_format(client, db_factory, csrf_for):
    cookie = make_admin_logged_in(client, db_factory, csrf_for)
    r = client.post(
        "/admin/users",
        data={"username": "ab", "csrf_token": csrf_for(cookie)},
        cookies={"session": cookie},
    )
    assert r.status_code == 400
    r2 = client.post(
        "/admin/users",
        data={"username": "with spaces", "csrf_token": csrf_for(cookie)},
        cookies={"session": cookie},
    )
    assert r2.status_code == 400


def test_regular_user_cannot_create(client, db_factory, csrf_for):
    cookie = make_regular_logged_in(client, db_factory, csrf_for)
    r = client.post(
        "/admin/users",
        data={"username": "evil", "csrf_token": csrf_for(cookie)},
        cookies={"session": cookie},
    )
    assert r.status_code == 403


def test_admin_can_delete_other_user(client, db_factory, csrf_for):
    cookie = make_admin_logged_in(client, db_factory, csrf_for)
    client.post(
        "/admin/users",
        data={"username": "victim", "csrf_token": csrf_for(cookie)},
        cookies={"session": cookie},
    )

    with db_factory() as s:
        from sqlalchemy import select
        victim = s.scalars(select(User).where(User.username == "victim")).one()

    r = client.post(
        f"/admin/users/{victim.id}/delete",
        data={"csrf_token": csrf_for(cookie)},
        cookies={"session": cookie},
    )
    assert r.status_code == 303

    with db_factory() as s:
        from sqlalchemy import select
        gone = s.scalars(select(User).where(User.username == "victim")).first()
        assert gone is None


def test_admin_cannot_delete_themselves(client, db_factory, csrf_for):
    cookie = make_admin_logged_in(client, db_factory, csrf_for)
    with db_factory() as s:
        from sqlalchemy import select
        me = s.scalars(select(User).where(User.username == "root")).one()
    r = client.post(
        f"/admin/users/{me.id}/delete",
        data={"csrf_token": csrf_for(cookie)},
        cookies={"session": cookie},
    )
    assert r.status_code == 400
