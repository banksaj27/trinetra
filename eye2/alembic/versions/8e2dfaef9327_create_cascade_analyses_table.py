"""create cascade_analyses table

Revision ID: 8e2dfaef9327
Revises: 001
Create Date: 2026-04-25 17:47:44.702274

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '8e2dfaef9327'
down_revision: Union[str, None] = '001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'cascade_analyses',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('cascade_id', sa.String(length=64), nullable=False),
        sa.Column('observation_id', sa.String(length=255), nullable=False),
        sa.Column('root_asset_id', sa.UUID(), nullable=False),
        sa.Column('analysis_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('total_population_impacted', sa.Integer(), server_default='0', nullable=False),
        sa.Column('critical_facilities_impacted', sa.Integer(), server_default='0', nullable=False),
        sa.Column('restoration_priority', sa.Integer(), server_default='0', nullable=False),
        sa.Column('priority_score', sa.Float(), server_default='0.0', nullable=False),
        sa.Column('hours_to_first_critical_failure', sa.Float(), server_default='0.0', nullable=False),
        sa.Column('severity_multiplier', sa.Float(), server_default='0.0', nullable=False),
        sa.Column('urgency_multiplier', sa.Float(), server_default='0.0', nullable=False),
        sa.Column('cascade', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('cascade_id'),
    )
    op.create_index('ix_cascade_analyses_created_at', 'cascade_analyses', ['created_at'], unique=False)
    op.create_index('ix_cascade_analyses_priority_score', 'cascade_analyses', ['priority_score'], unique=False)
    op.create_index('ix_cascade_analyses_root_asset_id', 'cascade_analyses', ['root_asset_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_cascade_analyses_root_asset_id', table_name='cascade_analyses')
    op.drop_index('ix_cascade_analyses_priority_score', table_name='cascade_analyses')
    op.drop_index('ix_cascade_analyses_created_at', table_name='cascade_analyses')
    op.drop_table('cascade_analyses')
