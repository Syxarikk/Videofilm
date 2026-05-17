from app.metadata.types import MetadataMatch, AudioTrack


def test_metadata_match_is_frozen_dataclass():
    m = MetadataMatch(
        source="tmdb", external_id=123, title="X", year=2020,
        kind="movie", description=None, poster_url=None,
        genres=[], score=0.9,
    )
    assert m.title == "X"
    import dataclasses
    assert dataclasses.is_dataclass(m)


def test_audio_track_basic():
    t = AudioTrack(index=0, codec="aac", language="rus", title="Дубляж", channels=6)
    assert t.language == "rus"
    assert t.channels == 6
