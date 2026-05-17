"""Episodes for series support

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "episodes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("series_id", sa.Integer(),
                  sa.ForeignKey("media_items.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("episode", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("audio_tracks", sa.JSON(), nullable=True),
        sa.Column("tmdb_episode_id", sa.Integer(), nullable=True),
        sa.Column("air_date", sa.Date(), nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_episodes_series_id", "episodes", ["series_id"])
    op.create_index("ix_episodes_series_season_episode", "episodes",
                    ["series_id", "season", "episode"], unique=True)

    op.create_table(
        "episode_watch_progress",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("episode_id", sa.Integer(),
                  sa.ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("audio_track_index", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_episode_watch_progress_user_episode", "episode_watch_progress",
                    ["user_id", "episode_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_episode_watch_progress_user_episode",
                  table_name="episode_watch_progress")
    op.drop_table("episode_watch_progress")
    op.drop_index("ix_episodes_series_season_episode", table_name="episodes")
    op.drop_index("ix_episodes_series_id", table_name="episodes")
    op.drop_table("episodes")
