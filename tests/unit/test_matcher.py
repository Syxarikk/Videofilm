from unittest.mock import MagicMock

from app.metadata.matcher import find_match, _is_confident, _normalize
from app.metadata.types import MetadataMatch
from app.torrents.title_parser import ParsedTitle


def _make_tmdb_match(title="X", year=2020, score=1.0):
    return MetadataMatch(
        source="tmdb", external_id=1, title=title, year=year,
        kind="movie", description=None, poster_url=None,
        genres=[], score=score,
    )


def test_normalize_lowercase_and_strip_punct():
    assert _normalize("Inception (2010)!") == "inception 2010"


def test_is_confident_with_year_match():
    parsed = ParsedTitle(title="Inception", year=2010, season=None, episode=None, kind_hint="movie")
    top = _make_tmdb_match(title="Inception", year=2010)
    assert _is_confident(top, parsed)


def test_is_confident_with_year_mismatch():
    parsed = ParsedTitle(title="Inception", year=2010, season=None, episode=None, kind_hint="movie")
    top = _make_tmdb_match(title="Inception", year=2015)
    assert not _is_confident(top, parsed)


def test_is_confident_no_year_requires_higher_similarity():
    parsed = ParsedTitle(title="Some Long Title", year=None, season=None, episode=None, kind_hint=None)
    top_low = _make_tmdb_match(title="Some Other Title", year=None)
    assert not _is_confident(top_low, parsed)

    top_high = _make_tmdb_match(title="Some Long Title", year=None)
    assert _is_confident(top_high, parsed)


def test_find_match_uses_tmdb_first_on_confident():
    parsed = ParsedTitle(title="Inception", year=2010, season=None, episode=None, kind_hint="movie")
    tmdb = MagicMock()
    tmdb.search.return_value = [{"id": 27205, "title": "Inception", "release_date": "2010-07-15"}]
    tmdb.get_movie.return_value = _make_tmdb_match(title="Inception", year=2010)
    kp = MagicMock()
    m = find_match(parsed, tmdb=tmdb, kinopoisk=kp)
    assert m is not None
    assert m.source == "tmdb"
    assert m.title == "Inception"
    kp.search.assert_not_called()


def test_find_match_falls_back_to_kinopoisk_if_tmdb_empty():
    parsed = ParsedTitle(title="Союзмультфильм", year=1970,
                         season=None, episode=None, kind_hint="movie")
    tmdb = MagicMock(); tmdb.search.return_value = []
    kp = MagicMock()
    kp.quota_ok.return_value = True
    kp.search.return_value = [{"filmId": 100, "nameRu": "Союзмультфильм", "year": "1970"}]
    kp.get_film.return_value = MetadataMatch(
        source="kinopoisk", external_id=100, title="Союзмультфильм", year=1970,
        kind="cartoon", description=None, poster_url=None, genres=[], score=1.0,
    )
    m = find_match(parsed, tmdb=tmdb, kinopoisk=kp)
    assert m is not None
    assert m.source == "kinopoisk"


def test_find_match_returns_none_when_both_empty():
    parsed = ParsedTitle(title="Nothing", year=None, season=None, episode=None, kind_hint=None)
    tmdb = MagicMock(); tmdb.search.return_value = []
    kp = MagicMock(); kp.quota_ok.return_value = True; kp.search.return_value = []
    assert find_match(parsed, tmdb=tmdb, kinopoisk=kp) is None


def test_find_match_skips_tmdb_when_client_none():
    parsed = ParsedTitle(title="X", year=2020, season=None, episode=None, kind_hint="movie")
    kp = MagicMock()
    kp.quota_ok.return_value = True
    kp.search.return_value = [{"filmId": 1, "nameRu": "X", "year": "2020"}]
    kp.get_film.return_value = MetadataMatch(
        source="kinopoisk", external_id=1, title="X", year=2020,
        kind="movie", description=None, poster_url=None, genres=[], score=1.0,
    )
    m = find_match(parsed, tmdb=None, kinopoisk=kp)
    assert m is not None and m.source == "kinopoisk"


def test_find_match_returns_none_when_no_clients():
    parsed = ParsedTitle(title="X", year=2020, season=None, episode=None, kind_hint="movie")
    assert find_match(parsed, tmdb=None, kinopoisk=None) is None


def test_find_match_skips_kinopoisk_when_quota_exhausted():
    parsed = ParsedTitle(title="X", year=2020, season=None, episode=None, kind_hint="movie")
    tmdb = MagicMock(); tmdb.search.return_value = []
    kp = MagicMock(); kp.quota_ok.return_value = False
    m = find_match(parsed, tmdb=tmdb, kinopoisk=kp)
    assert m is None
    kp.search.assert_not_called()
