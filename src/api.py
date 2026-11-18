"""FastAPI service exposing 5-second telemetry data for all 5 machines + live dashboard.

Run locally:
    uvicorn src.api:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from src.config import settings
from src.generator import TelemetryGenerator
from src.models import AlertLevel, MachineMonitorRecord, TelemetryEvent
from src.thresholds import evaluate_telemetry, get_thresholds_spec

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("telemetry.api")

STATIC_DIR = Path(__file__).resolve().parent / "static"


class TelemetryStore:
    """In-memory store holding latest readings, rolling history, and active alerts."""

    def __init__(self, max_records: int) -> None:
        self._lock = asyncio.Lock()
        self._latest_events: dict[str, TelemetryEvent] = {}
        self._latest_records: dict[str, MachineMonitorRecord] = {}
        self._previous_rpm: dict[str, int] = {}
        self._history: deque[TelemetryEvent] = deque(maxlen=max_records)
        self._alerts: deque[MachineMonitorRecord] = deque(maxlen=max_records)
        self._total_events_generated: int = 0
        self._last_updated: Optional[str] = None

    async def ingest_batch(self, events: list[TelemetryEvent]) -> None:
        async with self._lock:
            for event in events:
                prev_rpm = self._previous_rpm.get(event.machine_id)
                record = evaluate_telemetry(event, previous_rpm=prev_rpm)

                self._previous_rpm[event.machine_id] = event.rpm
                self._latest_events[event.machine_id] = event
                self._latest_records[event.machine_id] = record
                self._history.append(event)
                self._total_events_generated += 1

                if record.level != AlertLevel.NORMAL:
                    self._alerts.appendleft(record)

            if events:
                self._last_updated = events[-1].timestamp

    async def get_latest_events(self) -> list[TelemetryEvent]:
        async with self._lock:
            return [
                self._latest_events[mid]
                for mid in settings.machine_ids
                if mid in self._latest_events
            ]

    async def get_machine_event(self, machine_id: str) -> Optional[TelemetryEvent]:
        async with self._lock:
            return self._latest_events.get(machine_id)

    async def get_history(
        self, machine_id: Optional[str] = None, limit: int = 50
    ) -> list[TelemetryEvent]:
        async with self._lock:
            items = list(self._history)
        if machine_id:
            items = [e for e in items if e.machine_id == machine_id]
        return list(reversed(items[-limit:]))

    async def get_monitor_snapshot(self) -> dict:
        async with self._lock:
            records = [
                self._latest_records[mid]
                for mid in settings.machine_ids
                if mid in self._latest_records
            ]
            last_updated = self._last_updated
            total_generated = self._total_events_generated

        summary = {
            "total_machines": len(records),
            "normal": sum(1 for r in records if r.level == AlertLevel.NORMAL),
            "warning": sum(1 for r in records if r.level == AlertLevel.WARNING),
            "critical": sum(1 for r in records if r.level == AlertLevel.CRITICAL),
        }
        return {
            "factory_id": settings.factory_id,
            "interval_seconds": settings.generation_interval_seconds,
            "last_updated": last_updated,
            "total_events_generated": total_generated,
            "summary": summary,
            "machines": [r.model_dump() for r in records],
        }

    async def get_alerts(
        self, level: Optional[AlertLevel] = None, limit: int = 50
    ) -> list[MachineMonitorRecord]:
        async with self._lock:
            items = list(self._alerts)
        if level:
            items = [r for r in items if r.level == level]
        return items[:limit]

    async def stats(self) -> dict:
        async with self._lock:
            return {
                "machines_tracked": len(self._latest_events),
                "total_events_generated": self._total_events_generated,
                "history_size": len(self._history),
                "alerts_recorded": len(self._alerts),
                "last_updated": self._last_updated,
            }


generator = TelemetryGenerator()
store = TelemetryStore(max_records=settings.history_max_records)


async def _generator_loop(stop_event: asyncio.Event) -> None:
    logger.info(
        "Telemetry generator started for %d machines (%s) every %.1fs",
        len(generator.machine_ids),
        ", ".join(generator.machine_ids),
        settings.generation_interval_seconds,
    )
    while not stop_event.is_set():
        batch = generator.generate_batch()
        await store.ingest_batch(batch)
        try:
            await asyncio.wait_for(
                stop_event.wait(), timeout=settings.generation_interval_seconds
            )
        except asyncio.TimeoutError:
            continue
    logger.info("Telemetry generator loop stopped.")


@asynccontextmanager
async def lifespan(_: FastAPI):
    stop_event = asyncio.Event()
    # Populate an immediate initial batch before serving requests
    await store.ingest_batch(generator.generate_batch())
    task = asyncio.create_task(_generator_loop(stop_event))
    try:
        yield
    finally:
        stop_event.set()
        await task


app = FastAPI(
    title="Factory Machine Telemetry API",
    description=(
        "Generates and serves 5-second telemetry for 5 factory machines, evaluates "
        "NORMAL/WARNING/CRITICAL thresholds, and feeds the ECS S3 collector & dashboard."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", include_in_schema=False)
@app.get("/dashboard", include_in_schema=False)
async def serve_dashboard() -> FileResponse:
    """Serve the live 5-machine monitoring dashboard."""
    dashboard_path = STATIC_DIR / "dashboard.html"
    if not dashboard_path.exists():
        raise HTTPException(status_code=404, detail="Dashboard UI not found")
    return FileResponse(dashboard_path)


@app.get("/health")
async def health_check() -> dict:
    """Health check endpoint used by AWS ECS & load balancers."""
    return {
        "status": "healthy",
        "service": "telemetry-api",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **(await store.stats()),
    }


@app.get("/api/v1/telemetry", response_model=list[TelemetryEvent])
async def get_latest_telemetry() -> list[TelemetryEvent]:
    """Return latest raw telemetry events for all 5 machines (consumed by S3 collector)."""
    return await store.get_latest_events()


@app.get("/api/v1/telemetry/history", response_model=list[TelemetryEvent])
async def get_telemetry_history(
    machine_id: Optional[str] = Query(default=None, description="Filter by machine_id"),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[TelemetryEvent]:
    """Return recent historical raw telemetry events."""
    return await store.get_history(machine_id=machine_id, limit=limit)


@app.get("/api/v1/telemetry/{machine_id}", response_model=TelemetryEvent)
async def get_machine_telemetry(machine_id: str) -> TelemetryEvent:
    """Return latest raw telemetry event for a single machine."""
    event = await store.get_machine_event(machine_id)
    if event is None:
        raise HTTPException(status_code=404, detail=f"Machine '{machine_id}' not found")
    return event


@app.get("/api/v1/monitor")
async def get_monitor_data() -> dict:
    """Return latest readings for all 5 machines enriched with threshold alert evaluations."""
    return await store.get_monitor_snapshot()


@app.get("/api/v1/alerts", response_model=list[MachineMonitorRecord])
async def get_alerts(
    level: Optional[AlertLevel] = Query(default=None, description="Filter by WARNING or CRITICAL"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[MachineMonitorRecord]:
    """Return recent WARNING and CRITICAL machine alert events."""
    return await store.get_alerts(level=level, limit=limit)


@app.get("/api/v1/thresholds")
async def get_thresholds() -> dict:
    """Return configured NORMAL, WARNING, and CRITICAL threshold rules."""
    return get_thresholds_spec()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.api:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
    )
