"""Initial schema with PostGIS

Revision ID: 001
Revises:
Create Date: 2026-04-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import geoalchemy2

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    op.create_table(
        "infrastructure_assets",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("asset_type", sa.String(50), nullable=False),
        sa.Column("criticality_tier", sa.Integer(), nullable=False),
        sa.Column(
            "geometry",
            geoalchemy2.Geometry("POINT", srid=4326, from_text="ST_GeomFromEWKT", name="geometry"),
            nullable=False,
        ),
        sa.Column(
            "service_area",
            geoalchemy2.Geometry("POLYGON", srid=4326, from_text="ST_GeomFromEWKT", name="geometry"),
            nullable=True,
        ),
        sa.Column("population_served", sa.Integer(), server_default="0", nullable=False),
        sa.Column("backup_power_hours", sa.Float(), server_default="0.0", nullable=False),
        sa.Column("metadata", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("hifld_id", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("hifld_id"),
    )
    op.create_index("ix_assets_geometry", "infrastructure_assets", ["geometry"], postgresql_using="gist")
    op.create_index("ix_assets_service_area", "infrastructure_assets", ["service_area"], postgresql_using="gist")
    op.create_index("ix_assets_asset_type", "infrastructure_assets", ["asset_type"])
    op.create_index("ix_assets_criticality_tier", "infrastructure_assets", ["criticality_tier"])

    op.create_table(
        "infrastructure_dependencies",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("upstream_asset_id", sa.UUID(), nullable=False),
        sa.Column("downstream_asset_id", sa.UUID(), nullable=False),
        sa.Column("dependency_type", sa.String(50), nullable=False),
        sa.Column("criticality", sa.String(20), nullable=False),
        sa.Column("failover_time_minutes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("inferred", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("confidence", sa.Float(), server_default="1.0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["upstream_asset_id"], ["infrastructure_assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["downstream_asset_id"], ["infrastructure_assets.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("upstream_asset_id", "downstream_asset_id", "dependency_type", name="uq_dependency_edge"),
        sa.CheckConstraint("upstream_asset_id != downstream_asset_id", name="ck_no_self_dependency"),
    )
    op.create_index("ix_deps_upstream", "infrastructure_dependencies", ["upstream_asset_id"])
    op.create_index("ix_deps_downstream", "infrastructure_dependencies", ["downstream_asset_id"])
    op.create_index("ix_deps_type", "infrastructure_dependencies", ["dependency_type"])


def downgrade() -> None:
    op.drop_table("infrastructure_dependencies")
    op.drop_table("infrastructure_assets")
    op.execute("DROP EXTENSION IF EXISTS postgis")
