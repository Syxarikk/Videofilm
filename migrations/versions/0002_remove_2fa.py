"""remove 2fa

Revision ID: 0002_remove_2fa
Revises: 0001
Create Date: 2026-05-04 16:20:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_remove_2fa"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("backup_codes")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("totp_enabled")
        batch_op.drop_column("totp_secret_encrypted")


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("totp_secret_encrypted", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("totp_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))

    op.create_table(
        "backup_codes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("code_hash", sa.String(length=255), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("backup_codes", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_backup_codes_user_id"), ["user_id"], unique=False)
