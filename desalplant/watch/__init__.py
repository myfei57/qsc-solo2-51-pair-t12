"""Graded alarms: limit reports graded into notices or shutdowns.

The service replaces one-shot per-component alarms with stateful incidents:

* :class:`Policy` decides, from duration, breach magnitude and source, whether
  a cause is a notice or a shutdown;
* :class:`WatchManager` buffers same-tick reports from concurrent sources,
  grades each tick once, compresses chatter flaps inside a window onto one
  incident (counting the flaps), and only clears on an explicit run of good
  ticks;
* acknowledged incidents are immutable history -- they are never silently
  removed by later data and are closed only by an explicit resolve.

Every raise, chatter flap, escalation, clear, acknowledge and resolve is an
append-only audit event carrying the grading basis, chatter count and clear
condition.
"""

from .errors import (
    ClearConditionsUnmetError,
    IncidentNotAcknowledgedError,
    InvalidPolicyError,
    StaleTickError,
    UnknownIncidentError,
    WatchError,
)
from .incident import CLEARED, OPEN, RESOLVED, SEPARATED, Incident
from .manager import WatchManager
from .policy import Policy
from .report import Report
from .tier import Tier
from .track import Track

CAUSE_TANK_LEVEL_HIGH = "tank_level_high"
CAUSE_TANK_LEVEL_LOW = "tank_level_low"
CAUSE_CHEM_LEVEL_LOW = "chemical_tank_level_low"
CAUSE_PUMP_PRESSURE_HIGH = "pump_pressure_high"
CAUSE_PUMP_STALLED = "pump_supply_stalled"

DEFAULT_POLICIES = (
    Policy(
        cause=CAUSE_TANK_LEVEL_HIGH,
        shutdown_excess=10.0,
        shutdown_after_ticks=5,
        clear_after_good_ticks=3,
        chatter_window_ticks=2,
    ),
    Policy(
        cause=CAUSE_TANK_LEVEL_LOW,
        shutdown_sources=frozenset({"level-switch", "product"}),
        shutdown_excess=5.0,
        shutdown_after_ticks=3,
        clear_after_good_ticks=3,
        chatter_window_ticks=2,
    ),
    Policy(
        cause=CAUSE_CHEM_LEVEL_LOW,
        shutdown_after_ticks=8,
        clear_after_good_ticks=4,
        chatter_window_ticks=2,
    ),
    Policy(
        cause=CAUSE_PUMP_PRESSURE_HIGH,
        shutdown_excess=15.0,
        shutdown_after_ticks=2,
        clear_after_good_ticks=3,
        chatter_window_ticks=1,
    ),
    Policy(
        cause=CAUSE_PUMP_STALLED,
        shutdown_sources=frozenset({"supply", "pump"}),
        clear_after_good_ticks=3,
        chatter_window_ticks=1,
    ),
)

DEFAULT_POLICY = Policy(
    cause="*",
    shutdown_excess=10.0,
    shutdown_after_ticks=5,
    clear_after_good_ticks=3,
    chatter_window_ticks=2,
)

__all__ = [
    "CLEARED",
    "OPEN",
    "RESOLVED",
    "SEPARATED",
    "CAUSE_CHEM_LEVEL_LOW",
    "CAUSE_PUMP_PRESSURE_HIGH",
    "CAUSE_PUMP_STALLED",
    "CAUSE_TANK_LEVEL_HIGH",
    "CAUSE_TANK_LEVEL_LOW",
    "DEFAULT_POLICIES",
    "DEFAULT_POLICY",
    "ClearConditionsUnmetError",
    "Incident",
    "IncidentNotAcknowledgedError",
    "InvalidPolicyError",
    "Policy",
    "Report",
    "StaleTickError",
    "Tier",
    "Track",
    "UnknownIncidentError",
    "WatchError",
    "WatchManager",
]
