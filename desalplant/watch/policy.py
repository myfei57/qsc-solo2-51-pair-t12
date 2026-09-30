"""Grading policy: how a cause becomes a notice or a shutdown."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .tier import Tier


@dataclass(frozen=True)
class Policy:
    """Grading basis for one alarm cause.

    A cause is in breach whenever a report carries a positive ``excess``. The
    first breach raises a :attr:`Tier.NOTICE`. It escalates to
    :attr:`Tier.SHUTDOWN` as soon as any of the three grading dimensions hits
    its bound:

    * ``shutdown_sources``      -- the reporting source is always decisive;
    * ``shutdown_excess``       -- the breach magnitude reaches the bound;
    * ``shutdown_after_ticks``  -- the breach persists for this many ticks.

    Clearing needs a run of ``clear_after_good_ticks`` consecutive good ticks.
    A breach that returns before ``chatter_window_ticks`` good ticks is a
    chatter flap: the same incident is kept and its chatter count grows.
    """

    cause: str
    shutdown_sources: frozenset[str] = frozenset()
    shutdown_excess: float | None = None
    shutdown_after_ticks: int | None = None
    clear_after_good_ticks: int = 3
    chatter_window_ticks: int = 2
    notice_after_ticks: int = 0

    def __post_init__(self) -> None:
        if not self.cause:
            raise ValueError("policy cause must not be empty")
        if self.clear_after_good_ticks < 1:
            raise ValueError("clear_after_good_ticks must be positive")
        if self.chatter_window_ticks < 0:
            raise ValueError("chatter_window_ticks must not be negative")
        if self.chatter_window_ticks >= self.clear_after_good_ticks:
            raise ValueError(
                "chatter_window_ticks must be shorter than clear_after_good_ticks"
            )
        if self.notice_after_ticks < 0:
            raise ValueError("notice_after_ticks must not be negative")
        if (
            self.shutdown_after_ticks is not None
            and self.shutdown_after_ticks < 1
        ):
            raise ValueError("shutdown_after_ticks must be positive")
        if self.shutdown_excess is not None and self.shutdown_excess <= 0:
            raise ValueError("shutdown_excess must be positive")

    def requires_shutdown(
        self, sources: Iterable[str], excess: float, bad_run: int
    ) -> bool:
        """Whether these dimensions meet the shutdown bound."""
        if self.shutdown_sources and any(
            source in self.shutdown_sources for source in sources
        ):
            return True
        if self.shutdown_excess is not None and excess >= self.shutdown_excess:
            return True
        if (
            self.shutdown_after_ticks is not None
            and bad_run >= self.shutdown_after_ticks
        ):
            return True
        return False

    def notice_due(self, bad_run: int) -> bool:
        """Whether a sustained-but-small breach is worth a notice yet."""
        return bad_run > self.notice_after_ticks

    def basis(self) -> list[dict[str, Any]]:
        """Ordered grading basis, used for audit and console display."""
        return [
            {
                "dimension": "source",
                "shutdown_when": "source_in",
                "shutdown_sources": sorted(self.shutdown_sources),
            },
            {
                "dimension": "excess",
                "shutdown_when": "excess_gte",
                "shutdown_excess": self.shutdown_excess,
            },
            {
                "dimension": "duration",
                "shutdown_when": "bad_ticks_gte",
                "shutdown_after_ticks": self.shutdown_after_ticks,
                "notice_after_ticks": self.notice_after_ticks,
            },
            {
                "dimension": "recovery",
                "shutdown_when": None,
                "clear_after_good_ticks": self.clear_after_good_ticks,
                "chatter_window_ticks": self.chatter_window_ticks,
            },
        ]

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly projection of the grading basis."""
        return {
            "cause": self.cause,
            "shutdown_sources": sorted(self.shutdown_sources),
            "shutdown_excess": self.shutdown_excess,
            "shutdown_after_ticks": self.shutdown_after_ticks,
            "notice_after_ticks": self.notice_after_ticks,
            "clear_after_good_ticks": self.clear_after_good_ticks,
            "chatter_window_ticks": self.chatter_window_ticks,
            "entry_tier": Tier.NOTICE.label(),
            "shutdown_tier": Tier.SHUTDOWN.label(),
        }
