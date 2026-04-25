import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.damage import router as damage_router
from app.services.damage_assessment import DamageAssessor

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)


def _async_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return database_url


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    model_path = os.getenv("DAMAGE_MODEL_PATH", "app/ml/best_model.pth")
    app.state.assessor = DamageAssessor(model_path=model_path)

    database_url = os.getenv("DATABASE_URL")
    engine = None
    if database_url:
        engine = create_async_engine(_async_database_url(database_url), pool_pre_ping=True)
        app.state.async_session_factory = async_sessionmaker(
            bind=engine,
            expire_on_commit=False,
        )
        logger.info("Configured async database session factory")
    else:
        logger.warning(
            "DATABASE_URL is not set; damage endpoints require request.assets or "
            "an app.state asset repository/session factory"
        )

    try:
        yield
    finally:
        if engine is not None:
            await engine.dispose()


app = FastAPI(title="TriNetra AI Backend", lifespan=lifespan)
app.include_router(damage_router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict[str, Optional[bool]]:
    assessor = getattr(app.state, "assessor", None)
    return {
        "ok": True,
        "damage_model_loaded": assessor.is_available if assessor else None,
    }
