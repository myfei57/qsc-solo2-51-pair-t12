"""Translate the one-shot component alarms into graded watch reports."""

from typing import TYPE_CHECKING

from ..chem.alarm import LOW_LEVEL as CHEM_LOW
from ..pump.alarm import HIGH_PRESSURE
from ..tank.alarm import HIGH_LEVEL, LOW_LEVEL
from ..watch import (
    CAUSE_CHEM_LEVEL_LOW,
    CAUSE_PUMP_PRESSURE_HIGH,
    CAUSE_PUMP_STALLED,
    CAUSE_TANK_LEVEL_HIGH,
    CAUSE_TANK_LEVEL_LOW,
    Report,
)

if TYPE_CHECKING:  # pragma: no cover
    from .plant import Plant


def component_reports(plant: "Plant", tick: int) -> list[Report]:
    """One graded report per monitored component at ``tick``.

    ``excess`` is the normalised magnitude past the limit; a healthy
    component reports zero excess so the watch manager can grade recovery.
    """
    reports: list[Report] = []

    level = plant.level.percent
    if level >= HIGH_LEVEL:
        reports.append(
            Report(tick, CAUSE_TANK_LEVEL_HIGH, "level-gauge", level - HIGH_LEVEL)
        )
    else:
        reports.append(Report(tick, CAUSE_TANK_LEVEL_HIGH, "level-gauge", 0.0))
    if level <= LOW_LEVEL:
        reports.append(
            Report(tick, CAUSE_TANK_LEVEL_LOW, "level-switch", LOW_LEVEL - level)
        )
    else:
        reports.append(Report(tick, CAUSE_TANK_LEVEL_LOW, "level-gauge", 0.0))

    chem = plant.chem_tank.level
    reports.append(
        Report(
            tick,
            CAUSE_CHEM_LEVEL_LOW,
            "doser",
            max(0.0, CHEM_LOW - chem),
        )
    )

    pump = plant.hp_pump
    if pump.stalled:
        reports.append(Report(tick, CAUSE_PUMP_STALLED, "supply", 1.0))
    else:
        reports.append(Report(tick, CAUSE_PUMP_STALLED, "supply", 0.0))
    reports.append(
        Report(
            tick,
            CAUSE_PUMP_PRESSURE_HIGH,
            "pump",
            max(0.0, pump.pressure - HIGH_PRESSURE),
        )
    )
    return reports
