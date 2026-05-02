from typing import Annotated

from fastapi import Depends, FastAPI, Form
from fastapi.testclient import TestClient

from app.csrf import generate_token, verify_csrf


def _make_app():
    app = FastAPI()

    @app.get("/get-token")
    def get_tok():
        return {"token": generate_token("session-A")}

    @app.post("/protected")
    def protected(
        csrf_token: Annotated[str, Form()],
        _: Annotated[None, Depends(verify_csrf)],
    ):
        return {"ok": True}

    return app


def test_verify_csrf_accepts_matching_token():
    """Когда session-key совпадает и токен валиден — POST проходит."""
    app = _make_app()
    with TestClient(app) as c:
        # Тест-фикстура устанавливает session-cookie через middleware Plan-1; тут руками подставим
        c.cookies.set("session", "session-A")
        token = generate_token("session-A")
        r = c.post("/protected", data={"csrf_token": token})
    assert r.status_code == 200


def test_verify_csrf_rejects_missing_token():
    app = _make_app()
    with TestClient(app) as c:
        c.cookies.set("session", "session-A")
        r = c.post("/protected", data={})
    assert r.status_code in (400, 422)


def test_verify_csrf_rejects_wrong_session():
    app = _make_app()
    with TestClient(app) as c:
        c.cookies.set("session", "session-DIFFERENT")
        token = generate_token("session-A")
        r = c.post("/protected", data={"csrf_token": token})
    assert r.status_code == 400
