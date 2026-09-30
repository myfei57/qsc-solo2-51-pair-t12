"""Key layout of the persisted control state."""

PLANT_STATE = "plant/state"
PLANT_STARTED_AT = "plant/started_at"
PLANT_STOPPED_AT = "plant/stopped_at"
PLANT_MAINTAINED_AT = "plant/maintained_at"

AUDIT_SEQUENCE = "audit/sequence"
AUDIT_EVENT_PREFIX = "audit/event/"
AUDIT_LAST_PREFIX = "audit/last/"

WATCH_TRACK_PREFIX = "watch/track/"


def audit_event_key(sequence: int) -> str:
    """Key holding the event written at ``sequence``."""
    if sequence < 1:
        raise ValueError("audit sequence starts at one")
    return f"{AUDIT_EVENT_PREFIX}{sequence:06d}"


def audit_last_key(kind: str) -> str:
    """Key holding the sequence of the latest event of ``kind``."""
    return f"{AUDIT_LAST_PREFIX}{kind}"


def watch_track_key(cause: str) -> str:
    """Key holding the graded-alarm tracking state of one cause."""
    if not cause:
        raise ValueError("watch cause must not be empty")
    return f"{WATCH_TRACK_PREFIX}{cause}"
