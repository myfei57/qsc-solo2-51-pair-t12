"""Per-cause tracking state kept by the watch manager."""

from dataclasses import dataclass, field
from typing import Any, Mapping

from .incident import Incident
from .report import Report


@dataclass
class Track:
    """Everything known about one cause.

    Reports for a tick are buffered and evaluated once, so concurrent sources
    reporting the same cause in one tick are graded together and can never
    upgrade an incident twice.
    """

    cause: str
    high_water: int = -1
    good_run: int = 0
    bad_run: int = 0
    pending_tick: int | None = None
    pending: list[Report] = field(default_factory=list)
    seen: set[tuple] = field(default_factory=set)
    incidents: list[Incident] = field(default_factory=list)

    def already_graded(self, tick: int) -> bool:
        """Whether the tick is finished, so a late report must be absorbed."""
        return tick <= self.high_water

    def open_incident(self) -> Incident | None:
        """The still-open incident of this cause, if any."""
        if self.incidents and self.incidents[-1].is_open:
            return self.incidents[-1]
        return None

    def find(self, incident_id: str) -> Incident | None:
        """Look an incident of this cause up by id."""
        for incident in reversed(self.incidents):
            if incident.id == incident_id:
                return incident
        return None

    def pending_ticks(self) -> tuple[int, ...]:
        """Ascending ticks currently buffered but not yet graded."""
        return tuple(sorted({report.tick for report in self.pending}))

    def take_pending(self, tick: int) -> list[Report]:
        """Remove and return the buffered reports for one evaluated tick."""
        buffered = [report for report in self.pending if report.tick == tick]
        self.pending = [report for report in self.pending if report.tick != tick]
        remaining = self.pending_ticks()
        self.pending_tick = max(remaining) if remaining else None
        return buffered

    @staticmethod
    def merge(reports: list[Report]) -> tuple[tuple[str, ...], float]:
        """Merge same-tick reports into the union of sources and worst excess."""
        sources = {report.source for report in reports}
        excess = max((report.excess for report in reports), default=0.0)
        return tuple(sorted(sources)), excess

    def to_dict(self) -> dict[str, Any]:
        """Persist the track. The seen-set is compacted per tick."""
        seen_by_tick: dict[int, list[list]] = {}
        for tick, cause, source, token in self.seen:
            seen_by_tick.setdefault(tick, []).append([source, token])
        return {
            "high_water": self.high_water,
            "good_run": self.good_run,
            "bad_run": self.bad_run,
            "pending_tick": self.pending_tick,
            "pending": [
                {
                    "tick": report.tick,
                    "cause": report.cause,
                    "source": report.source,
                    "excess": report.excess,
                    "token": report.token,
                }
                for report in self.pending
            ],
            "seen": seen_by_tick,
            "incidents": [incident.to_dict() for incident in self.incidents],
        }

    @classmethod
    def from_dict(cls, cause: str, row: Mapping[str, Any]) -> "Track":
        """Rebuild a track from a persisted row."""
        seen: set[tuple] = set()
        for tick_text, entries in row.get("seen", {}).items():
            for source, token in entries:
                seen.add((int(tick_text), cause, str(source), str(token)))
        return cls(
            cause=cause,
            high_water=int(row.get("high_water", -1)),
            good_run=int(row.get("good_run", 0)),
            bad_run=int(row.get("bad_run", 0)),
            pending_tick=(
                int(row["pending_tick"]) if row.get("pending_tick") is not None else None
            ),
            pending=[Report.from_dict(entry) for entry in row.get("pending", ())],
            seen=seen,
            incidents=[Incident.from_dict(entry) for entry in row.get("incidents", ())],
        )
