"""Telemetry generator producing readings every 5 seconds for 5 machines.

Generates events matching:
{
  "event_id": "UUID",
  "machine_id": "MACHINE_001",
  "factory_id": "FACTORY_001",
  "timestamp": "2026-11-18T10:00:00Z",
  "temperature": 65.5,
  "vibration": 2.5,
  "rpm": 1500,
  "status": "RUNNING"
}

Can be imported by the FastAPI server or run standalone via:
    python -m src.generator
"""

from __future__ import annotations

import json
import random
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from src.config import settings
from src.models import AlertLevel, TelemetryEvent


@dataclass
class _MachineState:
    regime: AlertLevel
    last_rpm: int
    ticks_remaining: int


class TelemetryGenerator:
    """Stateful generator simulating realistic NORMAL, WARNING, and CRITICAL machine behavior."""

    def __init__(
        self,
        factory_id: str | None = None,
        machine_ids: list[str] | None = None,
        seed: int | None = None,
    ) -> None:
        self.factory_id = factory_id or settings.factory_id
        self.machine_ids = machine_ids or list(settings.machine_ids)
        self._rng = random.Random(seed)
        self._states: dict[str, _MachineState] = {}

        for mid in self.machine_ids:
            self._states[mid] = _MachineState(
                regime=AlertLevel.NORMAL,
                last_rpm=self._rng.randint(1250, 1550),
                ticks_remaining=self._rng.randint(2, 5),
            )

    def _next_regime(self, current: AlertLevel) -> AlertLevel:
        """Transition realistically between NORMAL, WARNING, and CRITICAL regimes."""
        roll = self._rng.random()
        if current == AlertLevel.NORMAL:
            if roll < 0.72:
                return AlertLevel.NORMAL
            if roll < 0.92:
                return AlertLevel.WARNING
            return AlertLevel.CRITICAL
        if current == AlertLevel.WARNING:
            if roll < 0.55:
                return AlertLevel.NORMAL
            if roll < 0.85:
                return AlertLevel.WARNING
            return AlertLevel.CRITICAL
        # From CRITICAL, tend to recover to WARNING or NORMAL
        if roll < 0.50:
            return AlertLevel.WARNING
        if roll < 0.80:
            return AlertLevel.NORMAL
        return AlertLevel.CRITICAL

    def generate_for_machine(self, machine_id: str) -> TelemetryEvent:
        state = self._states.get(machine_id)
        if state is None:
            state = _MachineState(
                regime=AlertLevel.NORMAL,
                last_rpm=self._rng.randint(1250, 1550),
                ticks_remaining=3,
            )
            self._states[machine_id] = state

        if state.ticks_remaining <= 0:
            state.regime = self._next_regime(state.regime)
            state.ticks_remaining = self._rng.randint(2, 4)
        state.ticks_remaining -= 1

        if state.regime == AlertLevel.NORMAL:
            temperature = round(self._rng.uniform(45.0, 70.0), 1)
            vibration = round(self._rng.uniform(1.0, 4.0), 2)
            # Small stable drift within 1000–1800 RPM
            delta = self._rng.randint(-60, 60)
            rpm = max(1000, min(1800, state.last_rpm + delta))

        elif state.regime == AlertLevel.WARNING:
            temperature = round(self._rng.uniform(70.5, 84.8), 1)
            vibration = round(self._rng.uniform(4.1, 6.9), 2)
            # Noticeable RPM fluctuations (delta 170–310 or slightly outside 1000–1800)
            direction = self._rng.choice([-1, 1])
            delta = direction * self._rng.randint(170, 310)
            rpm = max(820, min(2080, state.last_rpm + delta))

        else:  # CRITICAL
            temperature = round(self._rng.uniform(85.5, 109.5), 1)
            vibration = round(self._rng.uniform(7.2, 11.9), 2)
            # Unstable RPM (large sudden swings or extreme speeds)
            direction = self._rng.choice([-1, 1])
            delta = direction * self._rng.randint(380, 650)
            rpm = max(450, min(2500, state.last_rpm + delta))

        state.last_rpm = rpm

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return TelemetryEvent(
            event_id=str(uuid.uuid4()),
            machine_id=machine_id,
            factory_id=self.factory_id,
            timestamp=timestamp,
            temperature=temperature,
            vibration=vibration,
            rpm=rpm,
            status="RUNNING",
        )

    def generate_batch(self) -> list[TelemetryEvent]:
        """Generate one telemetry event for every configured machine."""
        return [self.generate_for_machine(mid) for mid in self.machine_ids]


def main() -> None:
    """Standalone CLI runner printing 5-machine telemetry every 5 seconds."""
    generator = TelemetryGenerator()
    interval = settings.generation_interval_seconds
    print(
        f"Starting standalone telemetry generator for {len(generator.machine_ids)} machines "
        f"(interval={interval}s). Press Ctrl+C to stop."
    )
    try:
        while True:
            batch = generator.generate_batch()
            for event in batch:
                print(json.dumps(event.model_dump()))
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nTelemetry generator stopped.")


if __name__ == "__main__":
    main()
