import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class CascadeAnalysisRecord(Base):
    __tablename__ = "cascade_analyses"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    cascade_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    observation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    root_asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    analysis_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    total_population_impacted: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    critical_facilities_impacted: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    restoration_priority: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    priority_score: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="0.0"
    )
    hours_to_first_critical_failure: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="0.0"
    )
    severity_multiplier: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="0.0"
    )
    urgency_multiplier: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="0.0"
    )
    cascade: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_cascade_analyses_priority_score", "priority_score"),
        Index("ix_cascade_analyses_created_at", "created_at"),
        Index("ix_cascade_analyses_root_asset_id", "root_asset_id"),
    )

    def __repr__(self) -> str:
        return f"<CascadeAnalysisRecord {self.cascade_id} score={self.priority_score}>"
