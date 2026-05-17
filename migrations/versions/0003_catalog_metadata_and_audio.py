"""catalog metadata, genres, audio_tracks, audio_track_index

Revision ID: 0003
Revises: 0002_remove_2fa
Create Date: 2026-05-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002_remove_2fa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # media_items: добавить метаданные
    with op.batch_alter_table("media_items") as batch:
        batch.add_column(sa.Column("duration_seconds", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("description", sa.Text(), nullable=True))
        batch.add_column(sa.Column("poster_url", sa.String(length=1024), nullable=True))
        batch.add_column(sa.Column("year", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("kind", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("tmdb_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("kinopoisk_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column(
            "match_status", sa.String(length=16),
            nullable=False, server_default="pending",
        ))
        batch.add_column(sa.Column("match_source", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("audio_tracks", sa.JSON(), nullable=True))
        batch.create_index("ix_media_items_kind", ["kind"])
        batch.create_index("ix_media_items_year", ["year"])
        batch.create_index("ix_media_items_title", ["title"])

    # genres
    op.create_table(
        "genres",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=64), nullable=False, unique=True),
    )

    # media_item_genres (m2m)
    op.create_table(
        "media_item_genres",
        sa.Column("media_id", sa.Integer(),
                  sa.ForeignKey("media_items.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("genre_id", sa.Integer(),
                  sa.ForeignKey("genres.id", ondelete="CASCADE"),
                  primary_key=True),
    )
    op.create_index(
        "ix_media_item_genres_genre_id", "media_item_genres", ["genre_id"]
    )

    # watch_progress: audio_track_index
    with op.batch_alter_table("watch_progress") as batch:
        batch.add_column(sa.Column("audio_track_index", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("watch_progress") as batch:
        batch.drop_column("audio_track_index")

    op.drop_index("ix_media_item_genres_genre_id", table_name="media_item_genres")
    op.drop_table("media_item_genres")
    op.drop_table("genres")

    with op.batch_alter_table("media_items") as batch:
        batch.drop_index("ix_media_items_title")
        batch.drop_index("ix_media_items_year")
        batch.drop_index("ix_media_items_kind")
        batch.drop_column("audio_tracks")
        batch.drop_column("match_source")
        batch.drop_column("match_status")
        batch.drop_column("kinopoisk_id")
        batch.drop_column("tmdb_id")
        batch.drop_column("kind")
        batch.drop_column("year")
        batch.drop_column("poster_url")
        batch.drop_column("description")
        batch.drop_column("duration_seconds")
