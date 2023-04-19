"""Integration Status

Revision ID: dac99ae0c6fd
Revises: 0c2fe32b5649
Create Date: 2023-04-12 06:58:21.560292+00:00

"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "dac99ae0c6fd"
down_revision = "0c2fe32b5649"
branch_labels = None
depends_on = None

status_enum = sa.Enum("green", "red", name="external_integration_status")


def upgrade() -> None:
    # ### commands auto generated by Alembic ###
    op.create_table(
        "externalintegrationerrors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("time", sa.DateTime(), nullable=True),
        sa.Column("error", sa.Unicode(), nullable=True),
        sa.Column("external_integration_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["external_integration_id"],
            ["externalintegrations.id"],
            name="fk_error_externalintegrations_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    status_enum.create(op.get_bind())
    op.add_column(
        "externalintegrations",
        sa.Column(
            "status",
            status_enum,
            server_default="green",
            nullable=True,
        ),
    )
    op.add_column(
        "externalintegrations",
        sa.Column("last_status_update", sa.DateTime(), nullable=True),
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic ###
    op.drop_table("externalintegrationerrors")
    op.drop_column("externalintegrations", "last_status_update")
    op.drop_column("externalintegrations", "status")
    status_enum.drop(op.get_bind())
    # ### end Alembic commands ###
