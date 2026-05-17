import httpx
import respx
import pytest

from app.metadata.tmdb import TmdbClient
from app.metadata.types import MetadataMatch


@pytest.fixture
def client():
    return TmdbClient(api_key="fake-key")


@respx.mock
def test_search_movie_returns_results(client):
    respx.get("https://api.themoviedb.org/3/search/movie").mock(
        return_value=httpx.Response(200, json={
            "results": [
                {"id": 1, "title": "Inception", "release_date": "2010-07-16",
                 "overview": "A thief...", "poster_path": "/abc.jpg",
                 "genre_ids": [28, 18]},
            ],
        })
    )
    results = client.search("Inception", year=2010, kind_hint="movie")
    assert len(results) == 1
    assert results[0]["id"] == 1
    assert results[0]["title"] == "Inception"


@respx.mock
def test_search_tv_uses_tv_endpoint(client):
    route = respx.get("https://api.themoviedb.org/3/search/tv").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    client.search("Breaking Bad", year=None, kind_hint="tv")
    assert route.called


@respx.mock
def test_search_no_hint_uses_multi(client):
    route = respx.get("https://api.themoviedb.org/3/search/multi").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    client.search("Anything", year=None, kind_hint=None)
    assert route.called


@respx.mock
def test_search_returns_empty_on_error(client):
    respx.get("https://api.themoviedb.org/3/search/movie").mock(
        return_value=httpx.Response(401)
    )
    assert client.search("X", year=None, kind_hint="movie") == []


@respx.mock
def test_search_returns_empty_on_timeout(client):
    respx.get("https://api.themoviedb.org/3/search/movie").mock(
        side_effect=httpx.ReadTimeout("timeout")
    )
    assert client.search("X", year=None, kind_hint="movie") == []


@respx.mock
def test_get_movie_returns_metadata_match(client):
    respx.get("https://api.themoviedb.org/3/movie/27205").mock(
        return_value=httpx.Response(200, json={
            "id": 27205,
            "title": "Начало",
            "release_date": "2010-07-15",
            "overview": "Описание Начала на русском.",
            "poster_path": "/abc.jpg",
            "genres": [{"id": 28, "name": "Боевик"}, {"id": 18, "name": "Драма"}],
            "production_countries": [{"iso_3166_1": "US"}],
        })
    )
    m = client.get_movie(27205)
    assert isinstance(m, MetadataMatch)
    assert m.source == "tmdb"
    assert m.external_id == 27205
    assert m.title == "Начало"
    assert m.year == 2010
    assert m.kind == "movie"
    assert m.description == "Описание Начала на русском."
    assert m.poster_url == "https://image.tmdb.org/t/p/w500/abc.jpg"
    assert "Боевик" in m.genres and "Драма" in m.genres


@respx.mock
def test_get_movie_animation_us_is_cartoon(client):
    respx.get("https://api.themoviedb.org/3/movie/1").mock(
        return_value=httpx.Response(200, json={
            "id": 1, "title": "Toy Story", "release_date": "1995-11-22",
            "overview": "", "poster_path": "/p.jpg",
            "genres": [{"id": 16, "name": "Анимация"}, {"id": 35, "name": "Комедия"}],
            "production_countries": [{"iso_3166_1": "US"}],
        })
    )
    m = client.get_movie(1)
    assert m.kind == "cartoon"


@respx.mock
def test_get_tv_animation_jp_is_anime(client):
    respx.get("https://api.themoviedb.org/3/tv/2").mock(
        return_value=httpx.Response(200, json={
            "id": 2, "name": "Naruto", "first_air_date": "2002-10-03",
            "overview": "", "poster_path": "/n.jpg",
            "genres": [{"id": 16, "name": "Анимация"}],
            "origin_country": ["JP"],
        })
    )
    m = client.get_tv(2)
    assert m.kind == "anime"


@respx.mock
def test_get_tv_returns_series_kind(client):
    respx.get("https://api.themoviedb.org/3/tv/3").mock(
        return_value=httpx.Response(200, json={
            "id": 3, "name": "Breaking Bad", "first_air_date": "2008-01-20",
            "overview": "", "poster_path": "/bb.jpg",
            "genres": [{"id": 18, "name": "Драма"}],
            "origin_country": ["US"],
        })
    )
    m = client.get_tv(3)
    assert m.kind == "series"


@respx.mock
def test_get_movie_documentary(client):
    respx.get("https://api.themoviedb.org/3/movie/4").mock(
        return_value=httpx.Response(200, json={
            "id": 4, "title": "Doc", "release_date": "2020-01-01",
            "overview": "", "poster_path": None,
            "genres": [{"id": 99, "name": "Документальный"}],
            "production_countries": [{"iso_3166_1": "US"}],
        })
    )
    m = client.get_movie(4)
    assert m.kind == "documentary"
    assert m.poster_url is None
