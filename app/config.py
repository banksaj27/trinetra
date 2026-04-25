from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://trinetra:trinetra_dev@localhost:5432/trinetra"
    HIFLD_REGION: str = "PR"
    CENSUS_API_KEY: str | None = None
    MAPBOX_TOKEN: str = ""
    ADMIN_KEY: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
