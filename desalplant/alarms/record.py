"""Persistent, stateful alarm record.

The record is the single source of truth for a cause. Repeated reports of the
same cause fold into it (jitter suppression) and never create a second record
while it is live. Once acknowledged, the record is immutable history: later
data can only move it to CLEARED under explicit conditions, never delete or
silently rewrite it.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from ..ns.alarm import Severity


class AlarmState(str, Enum):
    """Lifecycle of an alarm record."""

    ACTIVE = "active"
    ACKNOWLEDGED = "acknowledged"
    CLEARED = "cleared"

    def __str__(self) -> str:
        return self.value


@dataclass
class AlarmRecord:
    """One stateful alarm keyed by its stable cause."""

    id: str
    cause: str
    source: str
    message: str
    severity: Severity
    state: AlarmState
    magnitude: float
    first_tick: int
    last_tick: int
    normal_ticks: int = 0
    jitter_count: int = 0
    escalations: int = 0
    grading_basis: str = ""
    acknowledged_at: int | None = None
    acknowledged_by: str = ""
    cleared_at: int | None = None
    clearing_condition: str = ""
    last_normal_tick: int | None = None

    def duration(self, current_tick: int) -> int:
        """Ticks elapsed since the record was first raised."""
        return current_tick - self.first_tick

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly projection shown on the console and persisted."""
        return {
            "id": self.id,
            "cause": self.cause,
            "source": self.source,
            "message": self.message,
            "severity": self.severity.label(),
            "state": str(self.state),
            "magnitude": round(self.magnitude, 4),
            "first_tick": self.first_tick,
            "last_tick": self.last_tick,
            "duration": self.duration(self.last_tick),
            "normal_ticks": self.normal_ticks,
            "jitter_count": self.jitter_count,
            "escalations": self.escalations,
            "grading_basis": self.grading_basis,
            "acknowledged": self.acknowledged_at is not None,
            "acknowledged_at": self.acknowledged_at,
            "acknowledged_by": self.acknowledged_by,
            "cleared_at": self.cleared_at,
            "clearing_condition": self.clearing_condition,
        }

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "AlarmRecord":
        """Rebuild a record from a persisted row."""
        state = AlarmState(str(row["state"]))
        severity = Severity[str(row["severity"]).upper()]
        return cls(
            id=str(row["id"]),
            cause=str(row["cause"]),
            source=str(row["source"]),
            message=str(row["message"]),
            severity=severity,
            state=state,
            magnitude=float(row["magnitude"]),
            first_tick=int(row["first_tick"]),
            last_tick=int(row["last_tick"]),
            normal_ticks=int(row.get("normal_ticks", 0)),
            jitter_count=int(row.get("jitter_count", 0)),
            escalations=int(row.get("escalations", 0)),
            grading_basis=str(row.get("grading_basis", "")),
            acknowledged_at=int(row["acknowledged_at"]) if row.get("acknowledged_at") is not None else None,
            acknowledged_by=str(row.get("acknowledged_by", "")),
            cleared_at=int(row["cleared_at"]) if row.get("cleared_at") is not None else None,
            clearing_condition=str(row.get("clearing_condition", "")),
            last_normal_tick=int(row["last_normal_tick"]) if row.get("last_normal_tick") is not None else None,
        )
