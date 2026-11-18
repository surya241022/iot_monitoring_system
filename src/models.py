"""Pydantic data models for machine telemetry and threshold evaluation."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class AlertLevel(str, Enum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class TelemetryEvent(BaseModel):
    """Exact telemetry payload produced every 5 seconds per machine."""

    event_id: str = Field(..., description="Unique UUIDv4 identifier for this reading")
    machine_id: str = Field(..., examples=["MACHINE_001"])
    factory_id: str = Field(..., examples=["FACTORY_001"])
    timestamp: str = Field(..., examples=["2026-11-18T10:00:00Z"])
    temperature: float = Field(..., description="Machine temperature in °C", examples=[65.5])
    vibration: float = Field(..., description="Machine vibration in mm/s", examples=[2.5])
    rpm: int = Field(..., description="Rotations per minute", examples=[1500])
    status: str = Field(default="RUNNING", examples=["RUNNING"])


class MetricEvaluation(BaseModel):
    metric: str
    value: float
    unit: str
    level: AlertLevel
    message: str


class MachineMonitorRecord(BaseModel):
    """Telemetry reading enriched with NORMAL / WARNING / CRITICAL evaluation."""

    event: TelemetryEvent
    level: AlertLevel
    rpm_delta: Optional[int] = None
    warnings: list[str] = Field(default_factory=list)
    metrics: list[MetricEvaluation] = Field(default_factory=list)
