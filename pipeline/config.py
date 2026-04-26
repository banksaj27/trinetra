from datetime import date
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings


REPO_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    MAPBOX_TOKEN: str = ""
    MODEL_PATH: str = str(REPO_ROOT / "eye1" / "app" / "ml" / "best_model.pth")
    DEFAULT_RADIUS_KM: float = 30.0
    DEFAULT_CROP_SIZE_METERS: float = 100.0
    DEFAULT_DISASTER_DATE: str = date.today().isoformat()
    RESULTS_CACHE_TTL_SECONDS: int = 3600
    INCLUDE_CROP_IMAGES: bool = True
    # Eye 3 — Twitter credentials
    TWITTER_USERNAME: str = ""
    TWITTER_EMAIL: str = ""
    TWITTER_PASSWORD: str = ""

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()

