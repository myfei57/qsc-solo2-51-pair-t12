"""Graded alarm tiers."""

from enum import IntEnum


class Tier(IntEnum):
    """Ordered response the line owes to a cause in breach."""

    NOTICE = 1
    SHUTDOWN = 2

    def label(self) -> str:
        """Lower-case name used in JSON payloads."""
        return self.name.lower()
