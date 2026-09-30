"""Tunable thresholds for grading, jitter suppression and clearing.

All durations are measured in logical ticks so behaviour is reproducible from
the ordered operation sequence alone; the service never reads the wall clock.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class AlarmPolicy:
    """Thresholds driving the graded alarm state machine."""

    # Jitter: a return within this many ticks of the last observation is a
    # repeat of the same alarm, not a new occurrence.
    jitter_window: int = 5

    # Clear: an unacknowledged alarm whose condition has been normal for this
    # many consecutive ticks is auto-cleared under this explicit condition.
    # It must outlast the jitter window so a short return is still folded into
    # the same record.
    clear_delay: int = 10

    # Magnitude: overshoot fractions over the threshold that step the base
    # severity up.
    warning_overshoot: float = 0.15
    critical_overshoot: float = 0.30

    # Duration: consecutive active ticks that escalate severity.
    medium_duration: int = 8
    long_duration: int = 20

    # Throttle audit jitter events to one per this many repeats so a chattering
    # condition does not flood the append-only record.
    jitter_audit_every: int = 5

    def __post_init__(self) -> None:
        if self.jitter_window < 1:
            raise ValueError("jitter_window must be positive")
        if self.clear_delay < 1:
            raise ValueError("clear_delay must be positive")
        if self.clear_delay < self.jitter_window:
            raise ValueError("clear_delay must be at least jitter_window")
        if not (0.0 < self.warning_overshoot < self.critical_overshoot):
            raise ValueError("overshoot thresholds must be ordered")
        if not (0 < self.medium_duration < self.long_duration):
            raise ValueError("duration thresholds must be ordered")
