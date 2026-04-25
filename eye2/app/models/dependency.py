import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base

VALID_DEPENDENCY_TYPES = ("power", "water", "communications", "road_access", "fuel")
VALID_CRITICALITY_VALUES = ("critical", "degraded_ops", "convenience")


class InfrastructureDependency(Base):
    __tablename__ = "infrastructure_dependencies"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    upstream_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("infrastructure_assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    downstream_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("infrastructure_assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    dependency_type: Mapped[str] = mapped_column(String(50), nullable=False)
    criticality: Mapped[str] = mapped_column(String(20), nullable=False)
    failover_time_minutes: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    inferred: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    confidence: Mapped[float] = mapped_column(Float, default=1.0, server_default="1.0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    upstream_asset = relationship(
        "InfrastructureAsset", foreign_keys=[upstream_asset_id], lazy="selectin"
    )
    downstream_asset = relationship(
        "InfrastructureAsset", foreign_keys=[downstream_asset_id], lazy="selectin"
    )

    __table_args__ = (
        UniqueConstraint(
            "upstream_asset_id",
            "downstream_asset_id",
            "dependency_type",
            name="uq_dependency_edge",
        ),
        CheckConstraint(
            "upstream_asset_id != downstream_asset_id",
            name="ck_no_self_dependency",
        ),
        Index("ix_deps_upstream", "upstream_asset_id"),
        Index("ix_deps_downstream", "downstream_asset_id"),
        Index("ix_deps_type", "dependency_type"),
    )

    def __repr__(self) -> str:
        return f"<Dependency {self.upstream_asset_id} -> {self.downstream_asset_id} ({self.dependency_type})>"
