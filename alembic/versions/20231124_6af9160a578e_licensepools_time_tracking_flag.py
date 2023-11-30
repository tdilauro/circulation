"""Licensepools time tracking flag

Revision ID: 6af9160a578e
Revises: 1e46a5bc33b5
Create Date: 2023-11-24 08:08:12.636590+00:00

"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "6af9160a578e"
down_revision = "1e46a5bc33b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "licensepools",
        sa.Column("should_track_playtime", sa.Boolean(), nullable=True, default=False),
    )
    session = op.get_bind()
    session.execute("UPDATE licensepools SET should_track_playtime=false")
    op.alter_column("licensepools", "should_track_playtime", nullable=False)


def downgrade() -> None:
    op.drop_column("licensepools", "should_track_playtime")
