import httpx
import respx
import pytest

from app.metadata.kinopoisk import KinopoiskClient
from app.metadata.types import MetadataMatch


@pytest.fixture
def client():
    return KinopoiskClient(api_key="fake-kp-key")


@respx.mock
def test_search_returns_results(client):
    respx.get(
        "https://kinopoiskapiunofficial.tech/api/v2.2/films/search-by-keyword"
    ).mock(return_value=httpx.Response(200, json={
        "films": [
            {"filmId": 100, "nameRu": "Тест", "year": "2020", "posterUrl": "https://kp/p.jpg"},
        ],
    }))
    results = client.search("Тест", year=2020)
    assert len(results) == 1
    assert results[0]["filmId"] == 100


@respx.mock
def test_search_uses_x_api_key_header(client):
    route = respx.get(
        "https://kinopoiskapiunofficial.tech/api/v2.2/films/search-by-keyword"
    ).mock(return_value=httpx.Response(200, json={"films": []}))
    client.search("X", year=None)
    assert route.called
    assert route.calls.last.request.headers["X-API-KEY"] == "fake-kp-key"


@respx.mock
def test_search_empty_on_error(client):
    respx.get(
        "https://kinopoiskapiunofficial.tech/api/v2.2/films/search-by-keyword"
    ).mock(return_value=httpx.Response(401))
    assert client.search("X", year=None) == []


@respx.mock
def test_search_increments_quota_counter(client):
    respx.get(
        "https://kinopoiskapiunofficial.tech/api/v2.2/films/search-by-keyword"
    ).mock(return_value=httpx.Response(200, json={"films": []}))
    assert client.quota_used_today() == 0
    client.search("X", year=None)
    assert client.quota_used_today() == 1


@respx.mock
def test_get_film_returns_metadata_match(client):
    respx.get(
        "https://kinopoiskapiunofficial.tech/api/v2.2/films/100"
    ).mock(return_value=httpx.Response(200, json={
        "kinopoiskId": 100,
        "nameRu": "Иван Васильевич меняет профессию",
        "year": 1973,
        "description": "Описание...",
        "posterUrl": "https://kp/p.jpg",
        "genres": [{"genre": "комедия"}, {"genre": "фантастика"}],
        "type": "FILM",
    }))
    m = client.get_film(100)
    assert isinstance(m, MetadataMatch)
    assert m.source == "kinopoisk"
    assert m.external_id == 100
    assert m.title == "Иван Васильевич меняет профессию"
    assert m.year == 1973
    assert m.kind == "movie"
    assert m.poster_url == "https://kp/p.jpg"
    assert "комедия" in m.genres


@respx.mock
def test_get_film_series_kind(client):
    respx.get(
        "https://kinopoiskapiunofficial.tech/api/v2.2/films/200"
    ).mock(return_value=httpx.Response(200, json={
        "kinopoiskId": 200, "nameRu": "Сериал X", "year": 2020,
        "description": "", "posterUrl": None,
        "genres": [{"genre": "драма"}], "type": "TV_SERIES",
    }))
    m = client.get_film(200)
    assert m.kind == "series"


@respx.mock
def test_get_film_returns_none_on_error(client):
    respx.get(
        "https://kinopoiskapiunofficial.tech/api/v2.2/films/999"
    ).mock(return_value=httpx.Response(404))
    assert client.get_film(999) is None
