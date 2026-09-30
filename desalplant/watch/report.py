"""One limit observation reported by a source."""

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class Report:
    """One observation of a monitored cause at one tick.

    ``excess`` is the normalised magnitude past the limit (zero means the
    reading is healthy). A cause may be reported by several sources on the
    same tick; ``token`` is an optional producer-side idempotency key.
    """

    tick: int
    cause: str
    source: str
    excess: float = 0.0
    token: str = ""

    def __post_init__(self) -> None:
        if not self.cause:
            raise ValueError("report cause must not be empty")
        if not self.source:
            raise ValueError("report source must not be empty")
        if self.tick < 0:
            raise ValueError("report tick must not be negative")
        if self.excess < 0:
            raise ValueError("report excess must not be negative")

    @property
    def breach(self) -> bool:
        """Whether the reading is past its limit."""
        return self.excess > 0.0

    def dedupe_key(self) -> tuple[int, str, str, str]:
        """Identity of a report for same-tick idempotent ingestion."""
        return (self.tick, self.cause, self.source, self.token)

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "Report":
        """Rebuild a report from a JSON mapping."""
        return cls(
            tick=int(row["tick"]),
            cause=str(row["cause"]),
            source=str(row["source"]),
            excess=float(row.get("excess", 0.0)),
            token=str(row.get("token", "")),
        )
