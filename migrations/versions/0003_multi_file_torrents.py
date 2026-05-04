"""multi-file torrents: drop unique on torrent_hash, add torrent_name

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-04 22:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0003'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """One torrent can now have many video files.

    SQLite-friendly approach: rename old table → create new with desired shape →
    copy data → drop old. Avoids the named-vs-unnamed unique-constraint dance
    that batch_alter_table struggles with for inline UNIQUE columns.
    """
    # 1. Add torrent_name (nullable initially so we can backfill)
    op.execute("ALTER TABLE media_items ADD COLUMN torrent_name VARCHAR(512)")
    # 2. Backfill from title — best we can do for existing rows
    op.execute("UPDATE media_items SET torrent_name = title")

    # 3. Recreate table without UNIQUE on torrent_hash, with composite unique on (torrent_hash, file_path),
    #    and torrent_name NOT NULL.
    op.execute("ALTER TABLE media_items RENAME TO media_items_old")
    op.execute("""
        CREATE TABLE media_items (
            id INTEGER PRIMARY KEY NOT NULL,
            torrent_hash VARCHAR(64) NOT NULL,
            torrent_name VARCHAR(512) NOT NULL,
            title VARCHAR(512) NOT NULL,
            file_path VARCHAR(1024) NOT NULL,
            size_bytes BIGINT NOT NULL,
            added_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            added_at DATETIME NOT NULL,
            CONSTRAINT uq_media_items_hash_path UNIQUE (torrent_hash, file_path)
        )
    """)
    op.execute("""
        INSERT INTO media_items
            (id, torrent_hash, torrent_name, title, file_path, size_bytes, added_by, added_at)
        SELECT
            id, torrent_hash, torrent_name, title, file_path, size_bytes, added_by, added_at
        FROM media_items_old
    """)
    op.execute("DROP TABLE media_items_old")
    op.execute("CREATE INDEX ix_media_items_torrent_hash ON media_items (torrent_hash)")


def downgrade() -> None:
    """Rebuild original schema. Multiple rows per torrent_hash collapse to one (largest)."""
    op.execute("ALTER TABLE media_items RENAME TO media_items_old")
    op.execute("""
        CREATE TABLE media_items (
            id INTEGER PRIMARY KEY NOT NULL,
            torrent_hash VARCHAR(64) NOT NULL UNIQUE,
            title VARCHAR(512) NOT NULL,
            file_path VARCHAR(1024) NOT NULL,
            size_bytes BIGINT NOT NULL,
            added_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            added_at DATETIME NOT NULL
        )
    """)
    # Take the largest file per torrent_hash (mirrors pre-0003 scanner behavior)
    op.execute("""
        INSERT INTO media_items
            (id, torrent_hash, title, file_path, size_bytes, added_by, added_at)
        SELECT
            o.id, o.torrent_hash, o.title, o.file_path, o.size_bytes, o.added_by, o.added_at
        FROM media_items_old o
        INNER JOIN (
            SELECT torrent_hash, MAX(size_bytes) AS max_size
            FROM media_items_old
            GROUP BY torrent_hash
        ) m ON m.torrent_hash = o.torrent_hash AND m.max_size = o.size_bytes
        GROUP BY o.torrent_hash
    """)
    op.execute("DROP TABLE media_items_old")
