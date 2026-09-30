"""Monitored points: how each component reports an alarm cause.

Each monitor evaluates one component into a ``(present, magnitude, message)``
tuple. The magnitude is the relative overshoot over the alarm threshold, which
is the "magnitude" input of the graded decision. Thresholds are reused from the
component alarm modules so the two cannot drift apart.
"""

from collections.abc import Iterable
from typing import Any

from ..chem.alarm import LOW_LEVEL as CHEM_LOW_LEVEL
from ..pump.alarm import HIGH_PRESSURE
from ..tank.alarm import HIGH_LEVEL as TANK_HIGH_LEVEL
from ..tank.alarm import LOW_LEVEL as TANK_LOW_LEVEL
from .cause import Cause


def _tank_level_high(plant: Any) -> tuple[bool, float, str]:
    percent = plant.level.percent
    if percent >= TANK_HIGH_LEVEL:
        over = (percent - TANK_HIGH_LEVEL) / TANK_HIGH_LEVEL
        return True, over, "tank level high"
    return False, 0.0, ""


def _tank_level_low(plant: Any) -> tuple[bool, float, str]:
    percent = plant.level.percent
    if percent <= TANK_LOW_LEVEL:
        over = (TANK_LOW_LEVEL - percent) / TANK_LOW_LEVEL
        return True, over, "tank level low"
    return False, 0.0, ""


def _chem_level_low(plant: Any) -> tuple[bool, float, str]:
    level = plant.chem_tank.level
    if level <= CHEM_LOW_LEVEL:
        over = (CHEM_LOW_LEVEL - level) / CHEM_LOW_LEVEL
        return True, over, "chemical tank level low"
    return False, 0.0, ""


def _pump_pressure_high(plant: Any) -> tuple[bool, float, str]:
    pressure = plant.hp_pump.pressure
    if pressure > HIGH_PRESSURE:
        over = (pressure - HIGH_PRESSURE) / HIGH_PRESSURE
        return True, over, "pump pressure high"
    return False, 0.0, ""


def _pump_stalled(plant: Any) -> tuple[bool, float, str]:
    if plant.hp_pump.stalled:
        return True, 1.0, "pump supply stalled"
    return False, 0.0, ""


# (cause, source, evaluate(plant) -> (present, magnitude, message))
MONITORS = (
    (Cause.TANK_LEVEL_HIGH, "level", _tank_level_high),
    (Cause.TANK_LEVEL_LOW, "level", _tank_level_low),
    (Cause.CHEM_LEVEL_LOW, "chem_tank", _chem_level_low),
    (Cause.PUMP_PRESSURE_HIGH, "pump", _pump_pressure_high),
    (Cause.PUMP_STALLED, "pump", _pump_stalled),
)


def observations(plant: Any) -> Iterable[tuple[str, str, bool, float, str]]:
    """Evaluate every monitored point into registry observations."""
    for cause, source, evaluate in MONITORS:
        present, magnitude, message = evaluate(plant)
        yield str(cause), source, present, magnitude, message
