"""CLI runner for the full data-loading pipeline.

Usage:
    python -m app.data_loader.load_all --region PR [--clear-existing] [--demo]
    python -m app.data_loader.load_all --inspect-fields
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time

from sqlalchemy import text

from app.database import async_session_factory

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


async def _commit_or_rollback_step(session, step_name: str, operation):
    try:
        result = await operation()
        await session.commit()
        return result
    except Exception as exc:
        await session.rollback()
        logger.error("[%s] failed: %s", step_name, exc, exc_info=True)
        return None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TriNetra data loading pipeline")
    parser.add_argument("--region", default="PR", help="FIPS state code (default: PR)")
    parser.add_argument("--clear-existing", action="store_true", help="Delete all existing data first")
    parser.add_argument("--demo", action="store_true", help="Generate synthetic data instead of fetching HIFLD")
    parser.add_argument(
        "--inspect-fields",
        action="store_true",
        help="Fetch one unfiltered sample row from each HIFLD dataset and print its fields",
    )
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> None:
    t0 = time.perf_counter()

    if args.inspect_fields:
        from app.data_loader.area_loader import inspect_area_dataset_fields
        from app.data_loader.hifld_loader import inspect_all_dataset_fields

        await inspect_all_dataset_fields()
        await inspect_area_dataset_fields()
        return

    async with async_session_factory() as session:
        # Step 0: optionally clear
        if args.clear_existing:
            logger.info("[1/5] Clearing existing data ...")
            cleared = await _commit_or_rollback_step(
                session,
                "clear_existing",
                lambda: _clear_existing(session),
            )
            if cleared is not None:
                logger.info("  Done.")

        # Step 1: load assets
        if args.demo:
            logger.info("[2/5] Generating demo data ...")
            from app.data_loader.demo_generator import generate_demo_data

            summary = await _commit_or_rollback_step(
                session,
                "load_assets",
                lambda: generate_demo_data(session),
            ) or {}
        else:
            logger.info("[2/5] Loading HIFLD datasets for region=%s ...", args.region)
            from app.data_loader.hifld_loader import load_all_datasets

            summary = await _commit_or_rollback_step(
                session,
                "load_assets",
                lambda: load_all_datasets(session, region=args.region),
            ) or {}

        logger.info("  Asset summary: %s", summary)

        # Step 2: generate service areas
        logger.info("[3/5] Generating service areas ...")
        from app.services.spatial_inference import generate_service_areas

        sa_summary = await _commit_or_rollback_step(
            session,
            "generate_service_areas",
            lambda: generate_service_areas(session),
        ) or {}
        logger.info("  Service area summary: %s", sa_summary)

        # Step 3: load census (skip in demo mode)
        if not args.demo:
            logger.info("[4/5] Loading Census data and assigning population ...")
            from app.data_loader.census_loader import load_census_and_assign_population

            updated = await _commit_or_rollback_step(
                session,
                "load_census",
                lambda: load_census_and_assign_population(session),
            ) or 0
            logger.info("  Updated population for %d assets", updated)
        else:
            logger.info("[4/5] Skipping Census data in demo mode")

        # Step 4: infer dependency edges
        logger.info("[5/5] Inferring dependency edges ...")
        from app.services.spatial_inference import infer_dependency_edges

        edge_summary = await _commit_or_rollback_step(
            session,
            "infer_dependency_edges",
            lambda: infer_dependency_edges(session),
        ) or {}
        logger.info("  Edge summary: %s", edge_summary)

    elapsed = time.perf_counter() - t0
    logger.info("Pipeline complete in %.1fs", elapsed)


async def _clear_existing(session) -> None:
    await session.execute(text("DELETE FROM infrastructure_dependencies"))
    await session.execute(text("DELETE FROM infrastructure_assets"))


def main() -> None:
    args = _parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
