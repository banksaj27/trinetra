import uuid
from datetime import datetime

from geoalchemy2 import Geometry
from sqlalchemy import Index, Integer, String, Float, DateTime, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

VALID_ASSET_TYPES = (
    "substation",
    "hospital",
    "water_treatment",
    "cell_tower",
    "shelter",
    "fire_station",
    "police_station",
    "911_center",
    "school",
    "wastewater",
    "fuel_depot",
    "data_center",
    "ems_station",
)


class InfrastructureAsset(Base):
    __tablename__ = "infrastructure_assets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(50), nullable=False)
    criticality_tier: Mapped[int] = mapped_column(Integer, nullable=False)
    geometry = mapped_column(Geometry("POINT", srid=4326), nullable=False)
    service_area = mapped_column(Geometry("POLYGON", srid=4326), nullable=True)
    population_served: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    backup_power_hours: Mapped[float] = mapped_column(Float, default=0.0, server_default="0.0")
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    hifld_id: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_assets_geometry", "geometry", postgresql_using="gist"),
        Index("ix_assets_service_area", "service_area", postgresql_using="gist"),
        Index("ix_assets_asset_type", "asset_type"),
        Index("ix_assets_criticality_tier", "criticality_tier"),
    )

    def __repr__(self) -> str:
        return f"<Asset {self.name} ({self.asset_type})>"
