"""Contract violations raised by the graded alarm (watch) service."""

from ..errors import ContractError


class WatchError(ContractError):
    """Base class for every watch-service contract violation."""


class StaleTickError(WatchError):
    """Raised when a report carries a tick older than one already processed."""


class UnknownIncidentError(WatchError):
    """Raised when an incident id does not name a tracked alarm."""


class IncidentNotAcknowledgedError(WatchError):
    """Raised when an acknowledged-only action is attempted on an open incident."""


class ClearConditionsUnmetError(WatchError):
    """Raised when a resolve is attempted while the clear conditions still fail."""


class InvalidPolicyError(WatchError):
    """Raised when a grading policy contradicts its own bounds."""
