from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Lafufu store"
    database_url: str = "postgresql+psycopg2://marketplace_user:marketplace_password@db:5432/marketplace_db"
    secret_key: str = "change-me"
    session_cookie_name: str = "marketplace_session"
    uploads_dir: str = "app/static/uploads"
    max_file_size_mb: int = 5
    allowed_extensions: set[str] = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
