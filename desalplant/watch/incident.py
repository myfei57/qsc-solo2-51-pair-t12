"""Incident: one graded alarm lifetime for a cause."""

from dataclasses import dataclass
from typing import Any, Mapping

from ..ns.ids import new_id
from .policy import Policy
from .tier import Tier

OPEN = "open"
CLEARED = "cleared"
RESOLVED = "resolved"
SEPARATED = "separated"


@dataclass
class Incident:
    """Mutable record of one breach episode of one cause.

    Once :meth:`acknowledge` has been called the record is historical: data
    alone never mutates or removes it, and it is never auto-cleared. It can
    only be resolved explicitly after the clear conditions are met.
    """

    id: str
    cause: str
    raised_tick: int
    tick: int
    tier: Tier
    sources: tuple[str, ...]
    max_excess: float
    bad_run: int
    chatter_count: int
    state: str = OPEN
    acknowledged: bool = False
    acknowledged_tick: int | None = None
    last_breach_tick: int = 0
    closed_tick: int | None = None

    @classmethod
    def raise_new(cls, policy: Policy, tick: int, sources, excess: float) -> "Incident":
        """Open a fresh incident for the first breach of a cause."""
        return cls(
            id=new_id(),
            cause=policy.cause,
            raised_tick=tick,
            tick=tick,
            tier=Tier.NOTICE,
            sources=tuple(sorted(set(sources))),
            max_excess=excess,
            bad_run=1,
            chatter_count=0,
            last_breach_tick=tick,
        )

    def touch(self, tick: int, sources, excess: float) -> None:
        """Merge a same-tick breach observation into the incident."""
        self.tick = tick
        self.last_breach_tick = tick
        if excess > self.max_excess:
            self.max_excess = excess
        merged = set(self.sources)
        merged.update(sources)
        self.sources = tuple(sorted(merged))

    def escalate(self, tick: int) -> None:
        """Move the incident to the shutdown grade. Idempotent."""
        if self.tier < Tier.SHUTDOWN:
            self.tier = Tier.SHUTDOWN

    def mark_chatter(self, tick: int) -> None:
        """Record one flap back into breach inside the chatter window."""
        self.chatter_count += 1
        self.last_breach_tick = tick
        self.tick = tick

    def acknowledge(self, tick: int) -> bool:
        """Freeze the record as acknowledged history. Idempotent."""
        if self.acknowledged:
            return False
        self.acknowledged = True
        self.acknowledged_tick = tick
        return True

    def clear(self, tick: int) -> None:
        """Auto-clear an unacknowledged incident after a clean run."""
        self.state = CLEARED
        self.closed_tick = tick

    def resolve(self, tick: int) -> None:
        """Resolve an acknowledged incident once clear conditions hold."""
        self.state = RESOLVED
        self.closed_tick = tick

    def separate(self, tick: int) -> None:
        """Close an episode because the cause returned after the chatter window.

        Used for unacknowledged incidents whose recovery lasted longer than
        the chatter window: the later breach is a distinct episode, so the
        prior one is closed historically (never silently deleted).
        """
        self.state = SEPARATED
        self.closed_tick = tick

    @property
    def is_open(self) -> bool:
        """Whether the incident still needs attention."""
        return self.state == OPEN

    def clear_condition(self, policy: Policy) -> dict[str, Any]:
        """The explicit condition that governs clearing/resolving."""
        good_needed = policy.clear_after_good_ticks
        window = policy.chatter_window_ticks
        return {
            "requires": "consecutive_good_ticks",
            "good_ticks": good_needed,
            "chatter_window_ticks": window,
            "auto_clear": not self.acknowledged,
            "explicit_resolve": self.acknowledged,
        }

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly projection used by the console."""
        return {
            "id": self.id,
            "cause": self.cause,
            "state": self.state,
            "tier": self.tier.label(),
            "acknowledged": self.acknowledged,
            "raised_tick": self.raised_tick,
            "last_tick": self.tick,
            "last_breach_tick": self.last_breach_tick,
            "acknowledged_tick": self.acknowledged_tick,
            "closed_tick": self.closed_tick,
            "sources": list(self.sources),
            "max_excess": self.max_excess,
            "bad_run": self.bad_run,
            "chatter_count": self.chatter_count,
        }

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "Incident":
        """Rebuild an incident from a persisted row."""
        return cls(
            id=str(row["id"]),
            cause=str(row["cause"]),
            raised_tick=int(row["raised_tick"]),
            tick=int(row["last_tick"]),
            tier=Tier(int(row["tier"]) if str(row["tier"]).isdigit() else Tier[str(row["tier"]).upper()]),
            sources=tuple(row.get("sources", ())),
            max_excess=float(row.get("max_excess", 0.0)),
            bad_run=int(row.get("bad_run", 0)),
            chatter_count=int(row.get("chatter_count", 0)),
            state=str(row.get("state", OPEN)),
            acknowledged=bool(row.get("acknowledged", False)),
            acknowledged_tick=(
                int(row["acknowledged_tick"]) if row.get("acknowledged_tick") is not None else None
            ),
            last_breach_tick=int(row.get("last_breach_tick", 0)),
            closed_tick=(
                int(row["closed_tick"]) if row.get("closed_tick") is not None else None
            ),
        )
