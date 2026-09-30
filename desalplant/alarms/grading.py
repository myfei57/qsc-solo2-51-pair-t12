"""Graded severity decision from duration, magnitude and source.

The decision combines three independent inputs and takes the worst, so it is
monotonic: re-evaluating a persistent condition can only ever escalate, never
talk an alarm down. The returned basis is a stable, human-readable string
recorded in the audit and shown on the console.
"""

from ..ns.alarm import Severity
from .cause import SOURCE_FLOOR


def _magnitude_severity(magnitude: float, policy) -> tuple[Severity, str]:
    if magnitude >= policy.critical_overshoot:
        return Severity.CRITICAL, f"overshoot {magnitude:.2f}>={policy.critical_overshoot:.2f}"
    if magnitude >= policy.warning_overshoot:
        return Severity.WARNING, f"overshoot {magnitude:.2f}>={policy.warning_overshoot:.2f}"
    return Severity.INFO, f"overshoot {magnitude:.2f}<{policy.warning_overshoot:.2f}"


def _duration_severity(duration: int, policy) -> tuple[Severity, str]:
    if duration >= policy.long_duration:
        return Severity.CRITICAL, f"duration {duration}>={policy.long_duration}"
    if duration >= policy.medium_duration:
        return Severity.WARNING, f"duration {duration}>={policy.medium_duration}"
    return Severity.INFO, f"duration {duration}<{policy.medium_duration}"


def grade(
    magnitude: float,
    duration: int,
    source: str,
    policy,
) -> tuple[Severity, str]:
    """Decide the severity for an active condition.

    Returns the severity and a semicolon-joined basis explaining which input
    drove the decision. The caller is responsible for applying the result
    monotonically (only escalate) so a re-evaluation cannot downgrade history.
    """
    mag_sev, mag_basis = _magnitude_severity(magnitude, policy)
    dur_sev, dur_basis = _duration_severity(duration, policy)
    src_sev = SOURCE_FLOOR.get(source, Severity.INFO)
    severity = max(mag_sev, dur_sev, src_sev)
    parts = [mag_basis, dur_basis]
    if src_sev > Severity.INFO:
        parts.append(f"source {source} floor={src_sev.label()}")
    return severity, "; ".join(parts)
