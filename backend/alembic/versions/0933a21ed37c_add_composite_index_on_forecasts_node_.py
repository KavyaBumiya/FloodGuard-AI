"""add composite index on forecasts node_id target_time

Revision ID: 0933a21ed37c
Revises: 046c73cac147
Create Date: 2026-10-05 14:16:58.342173
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# PostGIS geometry columns render as geoalchemy2.types.Geometry; autogenerate
# does not add this import itself, so the template supplies it.
import geoalchemy2

revision: str = '0933a21ed37c'
down_revision: str | None = '046c73cac147'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Composite index for forecast queries by node and target_time
    op.create_index(
        'ix_forecasts_node_target',
        'forecasts',
        ['node_id', 'target_time'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_forecasts_node_target', table_name='forecasts')