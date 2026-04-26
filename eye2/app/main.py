from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import assets, cascade, dependencies, graph, loader
from app.database import async_session_factory
from app.services.graph_builder import graph_service

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    app.state.graph_service = graph_service
    logger.info("Building in-memory graph from database ...")
    async with async_session_factory() as session:
        await graph_service.rebuild(session)
    logger.info("Graph ready.")
    yield


app = FastAPI(
    title="TriNetra AI — Infrastructure Dependency Graph",
    description="REST API for geo-located infrastructure assets and their dependency relationships.",
    version="0.1.0",
    lifespan=lifespan,
)

# TODO: tighten allow_origins for production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(assets.router)
app.include_router(dependencies.router)
app.include_router(graph.router)
app.include_router(loader.router)
app.include_router(cascade.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
