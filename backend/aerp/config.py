from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT / ".env"), env_file_encoding="utf-8", extra="ignore")

    aerp_database_url: str = f"sqlite:///{ROOT / 'data' / 'aerp.db'}"
    aerp_auto_seed: bool = True
    aerp_demo_seed: int = 42
    aerp_host: str = "127.0.0.1"
    aerp_port: int = 8000
    aerp_cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173"
    aerp_sync_tasks: bool = False

    @property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.aerp_cors_origins.split(",") if item.strip()]

    @property
    def database_url(self) -> str:
        url = self.aerp_database_url
        if url.startswith("sqlite:///"):
            path = Path(url.removeprefix("sqlite:///"))
            if not path.is_absolute():
                path = ROOT / path
            path.parent.mkdir(parents=True, exist_ok=True)
            return f"sqlite:///{path}"
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
