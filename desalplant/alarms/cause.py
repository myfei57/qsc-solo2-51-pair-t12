"""Stable alarm causes used as idempotency keys.

A cause identifies a root condition independently of which source reported it.
Multiple sources reporting the same cause collapse onto one record, so a flurry
of reports can never open duplicate alarms for one underlying problem.
"""

from enum import Enum

from ..ns.alarm import Severity


class Cause(str, Enum):
    """Root conditions the alarm console can track."""

    TANK_LEVEL_HIGH = "tank.level.high"
    TANK_LEVEL_LOW = "tank.level.low"
    CHEM_LEVEL_LOW = "chem.level.low"
    PUMP_PRESSURE_HIGH = "pump.pressure.high"
    PUMP_STALLED = "pump.stalled"

    def __str__(self) -> str:
        return self.value


# Which component reports each cause.
SOURCE_OF: dict[Cause, str] = {
    Cause.TANK_LEVEL_HIGH: "level",
    Cause.TANK_LEVEL_LOW: "level",
    Cause.CHEM_LEVEL_LOW: "chem_tank",
    Cause.PUMP_PRESSURE_HIGH: "pump",
    Cause.PUMP_STALLED: "pump",
}

# Criticality floor per source: a critical source never grades below this.
# This is the "source" input of the graded decision.
SOURCE_FLOOR: dict[str, Severity] = {
    "pump": Severity.WARNING,       # critical machinery -> at least a warning
    "chem_tank": Severity.WARNING,  # consumable run-out -> at least a warning
    "level": Severity.INFO,
}
