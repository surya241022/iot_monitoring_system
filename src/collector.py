"""ECS-ready Telemetry Collector: polls the Telemetry API every 5 seconds and stores data in S3.

Architecture:
    Telemetry API (GET /api/v1/telemetry) ---> ECS Collector ---> Amazon S3 (JSONL partitioned by date/hour)

Local dry-run mode:
    Set LOCAL_DRY_RUN=true in .env to write partitioned files locally under ./local_s3_output
    without making any AWS API calls.

AWS ECS mode:
    Set LOCAL_DRY_RUN=false and attach an IAM Task Role with s3:PutObject permission on S3_BUCKET_NAME.

Run locally:
    python -m src.collector
"""

from __future__ import annotations

import json
import logging
import signal
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3
import httpx
from botocore.exceptions import BotoCoreError, ClientError

from src.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("telemetry.collector")


class S3TelemetryCollector:
    """Polls machine telemetry from the REST API and writes partitioned JSONL batches to S3."""

    def __init__(
        self,
        api_base_url: str | None = None,
        bucket_name: str | None = None,
        s3_prefix: str | None = None,
        interval_seconds: float | None = None,
        local_dry_run: bool | None = None,
        local_output_dir: Path | None = None,
    ) -> None:
        self.api_base_url = (api_base_url or settings.api_base_url).rstrip("/")
        self.telemetry_url = f"{self.api_base_url}/api/v1/telemetry"
        self.bucket_name = bucket_name or settings.s3_bucket_name
        self.s3_prefix = (s3_prefix or settings.s3_prefix).strip("/")
        self.interval_seconds = (
            interval_seconds if interval_seconds is not None else settings.collector_interval_seconds
        )
        self.local_dry_run = (
            local_dry_run if local_dry_run is not None else settings.local_dry_run
        )
        self.local_output_dir = local_output_dir or settings.local_s3_output_dir

        self._seen_event_ids: set[str] = set()
        self._seen_order: deque[str] = deque(maxlen=5000)
        self._running = True
        self._http_client = httpx.Client(timeout=10.0)
        self._s3_client = None if self.local_dry_run else self._create_s3_client()

    @staticmethod
    def _create_s3_client():
        """Create a boto3 S3 client.

        On AWS ECS, leave AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY unset (or placeholder)
        so boto3 automatically uses the ECS Task IAM Role.
        """
        kwargs: dict[str, Any] = {"region_name": settings.aws_region}
        access_key = settings.aws_access_key_id
        secret_key = settings.aws_secret_access_key
        if (
            access_key
            and secret_key
            and not access_key.startswith("your-")
            and not secret_key.startswith("your-")
        ):
            kwargs["aws_access_key_id"] = access_key
            kwargs["aws_secret_access_key"] = secret_key
        return boto3.client("s3", **kwargs)

    def stop(self) -> None:
        self._running = False

    def close(self) -> None:
        self._http_client.close()

    def fetch_telemetry(self) -> list[dict[str, Any]]:
        """Fetch latest 5-machine telemetry batch from the API."""
        response = self._http_client.get(self.telemetry_url)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError(f"Expected list of telemetry events, got {type(payload).__name__}")
        return payload

    def filter_new_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Deduplicate events by event_id so S3 does not receive repeated readings."""
        fresh: list[dict[str, Any]] = []
        for event in events:
            event_id = event.get("event_id")
            if not event_id or event_id in self._seen_event_ids:
                continue
            if len(self._seen_order) == self._seen_order.maxlen:
                evicted = self._seen_order.popleft()
                self._seen_event_ids.discard(evicted)
            self._seen_order.append(event_id)
            self._seen_event_ids.add(event_id)
            fresh.append(event)
        return fresh

    def build_s3_key(self, events: list[dict[str, Any]], now: datetime | None = None) -> str:
        """Build an Athena/Hive-style partitioned S3 object key."""
        ts = now or datetime.now(timezone.utc)
        factory_id = events[0].get("factory_id", settings.factory_id) if events else settings.factory_id
        batch_suffix = uuid.uuid4().hex[:8]
        filename = f"telemetry_{ts.strftime('%Y%m%dT%H%M%SZ')}_{batch_suffix}.jsonl"
        return (
            f"{self.s3_prefix}/"
            f"factory_id={factory_id}/"
            f"year={ts.strftime('%Y')}/"
            f"month={ts.strftime('%m')}/"
            f"day={ts.strftime('%d')}/"
            f"hour={ts.strftime('%H')}/"
            f"{filename}"
        )

    @staticmethod
    def serialize_jsonl(events: list[dict[str, Any]]) -> bytes:
        """Serialize events as newline-delimited JSON (ideal for S3 + Athena/Spark)."""
        lines = [json.dumps(event, separators=(",", ":")) for event in events]
        return ("\n".join(lines) + "\n").encode("utf-8")

    def upload_batch(self, events: list[dict[str, Any]]) -> str:
        """Store a batch of telemetry events to S3 (or local dry-run folder)."""
        s3_key = self.build_s3_key(events)
        body = self.serialize_jsonl(events)

        if self.local_dry_run:
            target = self.local_output_dir / s3_key
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
            logger.info("[DRY-RUN] Saved %d events locally -> %s", len(events), target)
            return str(target)

        assert self._s3_client is not None
        self._s3_client.put_object(
            Bucket=self.bucket_name,
            Key=s3_key,
            Body=body,
            ContentType="application/x-ndjson",
        )
        s3_uri = f"s3://{self.bucket_name}/{s3_key}"
        logger.info("Uploaded %d events -> %s", len(events), s3_uri)
        return s3_uri

    def collect_once(self) -> str | None:
        """Run one poll -> deduplicate -> upload cycle."""
        events = self.fetch_telemetry()
        new_events = self.filter_new_events(events)
        if not new_events:
            logger.debug("No new events since last poll; skipping upload.")
            return None
        return self.upload_batch(new_events)

    def run_forever(self) -> None:
        """Main ECS worker loop with graceful shutdown handling."""
        mode = "LOCAL DRY-RUN" if self.local_dry_run else f"AWS S3 (s3://{self.bucket_name}/{self.s3_prefix})"
        logger.info(
            "Starting Telemetry Collector | source=%s | target=%s | interval=%.1fs",
            self.telemetry_url,
            mode,
            self.interval_seconds,
        )

        while self._running:
            started = time.monotonic()
            try:
                self.collect_once()
            except httpx.HTTPError as exc:
                logger.warning("Failed to fetch telemetry from API (%s): %s", self.telemetry_url, exc)
            except (BotoCoreError, ClientError) as exc:
                logger.error("Failed to upload batch to S3: %s", exc)
            except Exception as exc:
                logger.exception("Unexpected error during collection cycle: %s", exc)

            elapsed = time.monotonic() - started
            sleep_time = max(0.0, self.interval_seconds - elapsed)
            # Sleep in small increments so SIGTERM exits promptly inside ECS
            end_time = time.monotonic() + sleep_time
            while self._running and time.monotonic() < end_time:
                time.sleep(min(0.5, end_time - time.monotonic()))

        logger.info("Telemetry Collector stopped cleanly.")


def main() -> None:
    collector = S3TelemetryCollector()

    def _handle_signal(signum: int, _frame: Any) -> None:
        logger.info("Received signal %d; shutting down collector...", signum)
        collector.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    try:
        collector.run_forever()
    finally:
        collector.close()


if __name__ == "__main__":
    main()
