"""Application configuration loaded from environment variables / .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root if present
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_csv(value: str | None, default: list[str]) -> list[str]:
    if not value:
        return default
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or default


@dataclass(frozen=True)
class Settings:
    # Generator
    factory_id: str = field(default_factory=lambda: os.getenv("FACTORY_ID", "FACTORY_001"))
    machine_ids: list[str] = field(
        default_factory=lambda: _parse_csv(
            os.getenv("MACHINE_IDS"),
            ["MACHINE_001", "MACHINE_002", "MACHINE_003", "MACHINE_004", "MACHINE_005"],
        )
    )
    generation_interval_seconds: float = field(
        default_factory=lambda: float(os.getenv("GENERATION_INTERVAL_SECONDS", "5"))
    )
    history_max_records: int = field(
        default_factory=lambda: int(os.getenv("HISTORY_MAX_RECORDS", "500"))
    )

    # API
    api_host: str = field(default_factory=lambda: os.getenv("API_HOST", "0.0.0.0"))
    api_port: int = field(default_factory=lambda: int(os.getenv("API_PORT", "8000")))
    api_base_url: str = field(
        default_factory=lambda: os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
    )

    # Collector
    collector_interval_seconds: float = field(
        default_factory=lambda: float(os.getenv("COLLECTOR_INTERVAL_SECONDS", "5"))
    )
    local_dry_run: bool = field(
        default_factory=lambda: _parse_bool(os.getenv("LOCAL_DRY_RUN"), default=True)
    )
    local_s3_output_dir: Path = field(
        default_factory=lambda: Path(os.getenv("LOCAL_S3_OUTPUT_DIR", "./local_s3_output"))
    )

    # AWS / S3
    aws_region: str = field(default_factory=lambda: os.getenv("AWS_REGION", "us-east-1"))
    aws_access_key_id: str | None = field(
        default_factory=lambda: os.getenv("AWS_ACCESS_KEY_ID") or None
    )
    aws_secret_access_key: str | None = field(
        default_factory=lambda: os.getenv("AWS_SECRET_ACCESS_KEY") or None
    )
    s3_bucket_name: str = field(
        default_factory=lambda: os.getenv("S3_BUCKET_NAME", "your-factory-telemetry-bucket")
    )
    s3_prefix: str = field(
        default_factory=lambda: os.getenv("S3_PREFIX", "telemetry/raw").strip("/")
    )


settings = Settings()
