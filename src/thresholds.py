"""Threshold rules and telemetry evaluator for NORMAL / WARNING / CRITICAL states.

Configured limits:
NORMAL:
  - Temperature: 45–70°C
  - Vibration: 1–4 mm/s
  - RPM: 1000–1800 (stable)

WARNING:
  - Temperature: 70–85°C
  - Vibration: 4–7 mm/s
  - RPM fluctuations

CRITICAL:
  - Temperature: 85–110°C
  - Vibration: 7–12 mm/s
  - Unstable RPM
"""

from __future__ import annotations

from typing import Optional

from src.models import AlertLevel, MachineMonitorRecord, MetricEvaluation, TelemetryEvent

# Threshold constants
TEMP_NORMAL_MIN = 45.0
TEMP_NORMAL_MAX = 70.0
TEMP_WARNING_MAX = 85.0
TEMP_CRITICAL_MAX = 110.0

VIB_NORMAL_MIN = 1.0
VIB_NORMAL_MAX = 4.0
VIB_WARNING_MAX = 7.0
VIB_CRITICAL_MAX = 12.0

RPM_NORMAL_MIN = 1000
RPM_NORMAL_MAX = 1800
RPM_WARNING_MIN = 800
RPM_WARNING_MAX = 2100
RPM_DELTA_WARNING = 150   # Tick-to-tick swing indicating RPM fluctuations
RPM_DELTA_CRITICAL = 350  # Severe swing indicating unstable RPM

_SEVERITY_ORDER = {
    AlertLevel.NORMAL: 0,
    AlertLevel.WARNING: 1,
    AlertLevel.CRITICAL: 2,
}


def get_thresholds_spec() -> dict:
    """Return human-readable threshold configuration for API and dashboard display."""
    return {
        "NORMAL": {
            "temperature_c": {"min": TEMP_NORMAL_MIN, "max": TEMP_NORMAL_MAX},
            "vibration_mm_s": {"min": VIB_NORMAL_MIN, "max": VIB_NORMAL_MAX},
            "rpm": {
                "min": RPM_NORMAL_MIN,
                "max": RPM_NORMAL_MAX,
                "max_delta": RPM_DELTA_WARNING,
            },
        },
        "WARNING": {
            "temperature_c": {"min": TEMP_NORMAL_MAX, "max": TEMP_WARNING_MAX},
            "vibration_mm_s": {"min": VIB_NORMAL_MAX, "max": VIB_WARNING_MAX},
            "rpm": {
                "description": "RPM fluctuations",
                "delta_range": [RPM_DELTA_WARNING, RPM_DELTA_CRITICAL],
                "outer_bounds": [[RPM_WARNING_MIN, RPM_NORMAL_MIN], [RPM_NORMAL_MAX, RPM_WARNING_MAX]],
            },
        },
        "CRITICAL": {
            "temperature_c": {"min": TEMP_WARNING_MAX, "max": TEMP_CRITICAL_MAX},
            "vibration_mm_s": {"min": VIB_WARNING_MAX, "max": VIB_CRITICAL_MAX},
            "rpm": {
                "description": "Unstable RPM",
                "delta_gt": RPM_DELTA_CRITICAL,
                "below": RPM_WARNING_MIN,
                "above": RPM_WARNING_MAX,
            },
        },
    }


def _evaluate_temperature(temp: float) -> MetricEvaluation:
    if temp > TEMP_WARNING_MAX:
        return MetricEvaluation(
            metric="temperature",
            value=temp,
            unit="°C",
            level=AlertLevel.CRITICAL,
            message=f"Critical temperature {temp:.1f}°C (exceeds {TEMP_WARNING_MAX:.0f}°C limit)",
        )
    if temp > TEMP_NORMAL_MAX:
        return MetricEvaluation(
            metric="temperature",
            value=temp,
            unit="°C",
            level=AlertLevel.WARNING,
            message=f"High temperature {temp:.1f}°C (warning range {TEMP_NORMAL_MAX:.0f}–{TEMP_WARNING_MAX:.0f}°C)",
        )
    if temp < TEMP_NORMAL_MIN:
        return MetricEvaluation(
            metric="temperature",
            value=temp,
            unit="°C",
            level=AlertLevel.WARNING,
            message=f"Low temperature {temp:.1f}°C (below {TEMP_NORMAL_MIN:.0f}°C normal range)",
        )
    return MetricEvaluation(
        metric="temperature",
        value=temp,
        unit="°C",
        level=AlertLevel.NORMAL,
        message=f"Normal ({TEMP_NORMAL_MIN:.0f}–{TEMP_NORMAL_MAX:.0f}°C)",
    )


def _evaluate_vibration(vib: float) -> MetricEvaluation:
    if vib > VIB_WARNING_MAX:
        return MetricEvaluation(
            metric="vibration",
            value=vib,
            unit="mm/s",
            level=AlertLevel.CRITICAL,
            message=f"Critical vibration {vib:.2f} mm/s (exceeds {VIB_WARNING_MAX:.0f} mm/s limit)",
        )
    if vib > VIB_NORMAL_MAX:
        return MetricEvaluation(
            metric="vibration",
            value=vib,
            unit="mm/s",
            level=AlertLevel.WARNING,
            message=f"Elevated vibration {vib:.2f} mm/s (warning range {VIB_NORMAL_MAX:.0f}–{VIB_WARNING_MAX:.0f} mm/s)",
        )
    if vib < VIB_NORMAL_MIN:
        return MetricEvaluation(
            metric="vibration",
            value=vib,
            unit="mm/s",
            level=AlertLevel.WARNING,
            message=f"Low vibration {vib:.2f} mm/s (below {VIB_NORMAL_MIN:.0f} mm/s normal range)",
        )
    return MetricEvaluation(
        metric="vibration",
        value=vib,
        unit="mm/s",
        level=AlertLevel.NORMAL,
        message=f"Normal ({VIB_NORMAL_MIN:.0f}–{VIB_NORMAL_MAX:.0f} mm/s)",
    )


def _evaluate_rpm(rpm: int, previous_rpm: Optional[int]) -> tuple[MetricEvaluation, Optional[int]]:
    delta = (rpm - previous_rpm) if previous_rpm is not None else None
    abs_delta = abs(delta) if delta is not None else 0

    if rpm < RPM_WARNING_MIN or rpm > RPM_WARNING_MAX or abs_delta > RPM_DELTA_CRITICAL:
        reason = (
            f"Unstable RPM {rpm} (Δ{delta:+d} RPM)"
            if delta is not None
            else f"Unstable RPM {rpm} (outside safe range)"
        )
        return (
            MetricEvaluation(
                metric="rpm",
                value=float(rpm),
                unit="RPM",
                level=AlertLevel.CRITICAL,
                message=reason,
            ),
            delta,
        )

    if rpm < RPM_NORMAL_MIN or rpm > RPM_NORMAL_MAX or abs_delta > RPM_DELTA_WARNING:
        reason = (
            f"RPM fluctuations detected: {rpm} (Δ{delta:+d} RPM)"
            if delta is not None
            else f"RPM fluctuations detected: {rpm} (outside {RPM_NORMAL_MIN}–{RPM_NORMAL_MAX})"
        )
        return (
            MetricEvaluation(
                metric="rpm",
                value=float(rpm),
                unit="RPM",
                level=AlertLevel.WARNING,
                message=reason,
            ),
            delta,
        )

    return (
        MetricEvaluation(
            metric="rpm",
            value=float(rpm),
            unit="RPM",
            level=AlertLevel.NORMAL,
            message=f"Stable ({RPM_NORMAL_MIN}–{RPM_NORMAL_MAX} RPM)",
        ),
        delta,
    )


def evaluate_telemetry(
    event: TelemetryEvent,
    previous_rpm: Optional[int] = None,
) -> MachineMonitorRecord:
    """Evaluate a single telemetry reading against NORMAL/WARNING/CRITICAL thresholds."""
    temp_eval = _evaluate_temperature(event.temperature)
    vib_eval = _evaluate_vibration(event.vibration)
    rpm_eval, rpm_delta = _evaluate_rpm(event.rpm, previous_rpm)

    metrics = [temp_eval, vib_eval, rpm_eval]
    overall = max((m.level for m in metrics), key=lambda lvl: _SEVERITY_ORDER[lvl])
    warnings = [m.message for m in metrics if m.level != AlertLevel.NORMAL]

    return MachineMonitorRecord(
        event=event,
        level=overall,
        rpm_delta=rpm_delta,
        warnings=warnings,
        metrics=metrics,
    )
