from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.app.domain.canonical import canonical_sha256, load_config_bundle

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: Literal["local", "demo", "eval", "prod", "test"] = "local"
    app_version: str = "dev"
    app_base_url: str = "http://localhost:8080"
    default_runtime_mode: Literal["snapshot", "live"] = "snapshot"
    log_level: str = "INFO"
    store_backend: Literal["local", "gcp"] = "local"
    local_store_dir: Path = Path(".local_store")
    google_cloud_project: str = ""
    google_cloud_location: str = "global"
    gcp_region: str = "asia-northeast3"
    gcs_bucket: str = ""
    firestore_database: str = "(default)"
    gemini_primary_model: str = "gemini-3.6-flash"
    gemini_lite_model: str = "gemini-3.5-flash-lite"
    gemini_embedding_model: str = "gemini-embedding-001"
    embedding_dim: int = 768
    embedding_cache_namespace: str = Field(default="v1", min_length=1, pattern=r"^[A-Za-z0-9_.-]+$")
    model_cache_namespace: str = Field(default="", pattern=r"^[A-Za-z0-9_.-]*$")
    demo_snapshot_version: str = ""
    demo_snapshot_dir: Path = Path("data/demo/current")
    app_enable_fault_injection: bool = False
    allow_live_model_calls: bool = False
    allow_live_ctgov_calls: bool = False
    live_retrieval_timeout_seconds: float = Field(default=18.0, ge=5.0, le=60.0)
    live_embedding_timeout_seconds: float = Field(default=6.0, ge=1.0, le=30.0)
    live_generation_attempt_timeout_seconds: float = Field(default=10.0, ge=2.0, le=30.0)
    live_compilation_candidate_limit: int = Field(default=4, ge=1, le=8)
    session_cost_cap_usd: float = Field(default=1.25, gt=0)
    daily_dev_cost_cap_usd: float = Field(default=10, gt=0)
    daily_demo_cost_cap_usd: float = Field(default=25, gt=0)
    total_app_cost_cap_usd: float = Field(default=180, gt=0)
    session_token_hmac_salt: str = "local-development-only-change-me"
    ip_hash_salt: str = "local-development-only-change-me"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


@lru_cache(maxsize=1)
def get_config_bundle() -> dict[str, dict[str, object]]:
    return load_config_bundle(REPOSITORY_ROOT / "config")


def config_bundle_hash() -> str:
    return canonical_sha256(get_config_bundle())
