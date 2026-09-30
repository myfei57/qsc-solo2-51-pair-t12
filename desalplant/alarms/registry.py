"""Stateful alarm registry: idempotent raising, jitter suppression and grading.

The registry is the single source of truth for alarm state. It is fed by sweeps
over the monitored components, persists every record across restarts, and logs
every state change (raise, escalate, jitter, acknowledge, clear) to the audit.

Timing discipline: every duration and jitter count is measured in logical ticks
and only advances when the clock advances, so re-sweeping at the same tick can
never double-count or silently age a record.
"""

from collections.abc import Iterable

from ..audit.recorder import Recorder
from ..clock import LogicalClock
from ..errors import AlarmImmutableError, UnknownKeyError
from ..ns.ids import new_id
from ..store import keys
from ..store.store import Store
from .grading import grade
from .policy import AlarmPolicy
from .record import AlarmRecord, AlarmState

Observation = tuple[str, str, bool, float, str]


class AlarmRegistry:
    """Keyed alarm records with idempotent, graded lifecycle handling."""

    def __init__(
        self,
        store: Store,
        clock: LogicalClock,
        recorder: Recorder,
        policy: AlarmPolicy | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._recorder = recorder
        self._policy = policy or AlarmPolicy()
        # Live records keyed by cause (ACTIVE or ACKNOWLEDGED).
        self._live: dict[str, AlarmRecord] = {}
        # Immutable history of CLEARED records, newest first.
        self._history: list[AlarmRecord] = []
        self._load()

    # ------------------------------------------------------------- persistence

    def _load(self) -> None:
        raw_live = self._store.get(keys.ALARM_RECORDS, {})
        if isinstance(raw_live, dict):
            for cause, row in raw_live.items():
                self._live[str(cause)] = AlarmRecord.from_dict(row)
        raw_history = self._store.get(keys.ALARM_HISTORY, [])
        if isinstance(raw_history, list):
            self._history = [AlarmRecord.from_dict(row) for row in raw_history]

    def _persist(self) -> None:
        self._store.put(
            keys.ALARM_RECORDS,
            {cause: rec.to_dict() for cause, rec in self._live.items()},
        )
        self._store.put(keys.ALARM_HISTORY, [rec.to_dict() for rec in self._history])

    # --------------------------------------------------------------- sweeping

    def sweep(self, observations: Iterable[Observation]) -> list[AlarmRecord]:
        """Ingest one round of observations and return the live records.

        Each observation is ``(cause, source, present, magnitude, message)``.
        A cause reported as present raises or refreshes the record; a cause
        reported absent (or not reported at all) is tracked as normal so the
        explicit auto-clear condition can be evaluated.
        """
        tick = self._clock.tick
        seen: set[str] = set()
        for cause, source, present, magnitude, message in observations:
            cause_key = str(cause)
            seen.add(cause_key)
            if present:
                self._raise(cause_key, source, magnitude, message, tick)
            else:
                self._note_normal(cause_key, tick)
        # Causes the sweep did not mention are treated as normal.
        for cause_key in list(self._live):
            if cause_key not in seen:
                self._note_normal(cause_key, tick)
        self._persist()
        return self.live()

    def _raise(self, cause: str, source: str, magnitude: float, message: str, tick: int) -> None:
        rec = self._live.get(cause)
        if rec is None:
            self._open(cause, source, magnitude, message, tick)
            return
        if rec.state is AlarmState.ACKNOWLEDGED:
            # Immutable history: later data only confirms the condition is seen
            # again; it must not rewrite or re-grade the acknowledged record.
            if tick > rec.last_tick:
                rec.last_tick = tick
            return
        # ACTIVE: refresh with jitter suppression and monotonic grading.
        self._refresh(rec, magnitude, message, tick)

    def _open(self, cause: str, source: str, magnitude: float, message: str, tick: int) -> None:
        severity, basis = grade(magnitude, 0, source, self._policy)
        rec = AlarmRecord(
            id=new_id(),
            cause=cause,
            source=source,
            message=message,
            severity=severity,
            state=AlarmState.ACTIVE,
            magnitude=magnitude,
            first_tick=tick,
            last_tick=tick,
            grading_basis=basis,
        )
        self._live[cause] = rec
        self._recorder.log(
            "alarm.raised",
            f"{cause} raised as {severity.label()} (magnitude={magnitude:.2f}): {basis}",
        )

    def _refresh(self, rec: AlarmRecord, magnitude: float, message: str, tick: int) -> None:
        # Jitter: the condition returned after a short absence. Fold it into the
        # same record and count the repeat instead of opening a new alarm.
        if (
            rec.normal_ticks > 0
            and rec.normal_ticks <= self._policy.jitter_window
            and tick > rec.last_tick
        ):
            rec.jitter_count += 1
            if rec.jitter_count % self._policy.jitter_audit_every == 0:
                self._recorder.log(
                    "alarm.jitter",
                    f"{rec.cause} chattered {rec.jitter_count} times",
                )
        if tick > rec.last_tick:
            rec.last_tick = tick
        rec.normal_ticks = 0
        rec.last_normal_tick = None
        rec.magnitude = magnitude
        if message:
            rec.message = message
        severity, basis = grade(magnitude, rec.duration(tick), rec.source, self._policy)
        if severity > rec.severity:
            old = rec.severity
            rec.severity = severity
            rec.grading_basis = basis
            rec.escalations += 1
            self._recorder.log(
                "alarm.escalated",
                f"{rec.cause} escalated {old.label()}->{severity.label()}: {basis}",
            )

    def _note_normal(self, cause: str, tick: int) -> None:
        rec = self._live.get(cause)
        if rec is None:
            return
        # Only age the normal period when time actually advanced; re-sweeping at
        # the same tick must never silently progress a record.
        if rec.last_normal_tick is not None and tick <= rec.last_normal_tick:
            return
        rec.normal_ticks += 1
        rec.last_normal_tick = tick
        if rec.state is AlarmState.ACTIVE and rec.normal_ticks >= self._policy.clear_delay:
            self._clear(rec, f"condition normal for {rec.normal_ticks} consecutive ticks", tick)
        # Acknowledged records are immutable: they are never auto-cleared by
        # later data and only leave the live set through an explicit operator
        # clear.

    # ------------------------------------------------------------ lifecycle

    def acknowledge(self, cause: str, by: str = "operator") -> AlarmRecord:
        """Mark a live record as acknowledged; idempotent."""
        rec = self._require_live(cause)
        if rec.state is AlarmState.ACKNOWLEDGED:
            return rec
        rec.state = AlarmState.ACKNOWLEDGED
        rec.acknowledged_at = self._clock.tick
        rec.acknowledged_by = by
        self._persist()
        self._recorder.log("alarm.acknowledged", f"{cause} acknowledged by {by}")
        return rec

    def clear(self, cause: str, reason: str = "") -> AlarmRecord:
        """Explicitly clear a live record under a stated condition; idempotent."""
        rec = self._live.get(str(cause))
        if rec is None:
            # Already cleared into history: the condition is already met.
            for history_rec in self._history:
                if history_rec.cause == str(cause):
                    return history_rec
            raise UnknownKeyError(f"no alarm for cause {cause}")
        condition = reason or "operator cleared"
        self._clear(rec, condition, self._clock.tick)
        self._persist()
        return rec

    def _clear(self, rec: AlarmRecord, condition: str, tick: int) -> None:
        rec.state = AlarmState.CLEARED
        rec.cleared_at = tick
        rec.clearing_condition = condition
        # Move the record out of the live set into immutable history.
        self._live.pop(rec.cause, None)
        self._history.insert(0, rec)
        self._recorder.log(
            "alarm.cleared",
            f"{rec.cause} cleared: {condition} "
            f"(jitter={rec.jitter_count}, duration={rec.duration(tick)}, "
            f"escalations={rec.escalations})",
        )

    def _require_live(self, cause: str) -> AlarmRecord:
        rec = self._live.get(str(cause))
        if rec is None:
            # Either never raised or already cleared into history.
            if any(r.cause == str(cause) for r in self._history):
                raise AlarmImmutableError(f"alarm {cause} is cleared history")
            raise UnknownKeyError(f"no live alarm for cause {cause}")
        return rec

    # ---------------------------------------------------------------- queries

    def live(self) -> list[AlarmRecord]:
        """Live records (ACTIVE or ACKNOWLEDGED), newest first."""
        return sorted(self._live.values(), key=lambda r: r.last_tick, reverse=True)

    def history(self) -> list[AlarmRecord]:
        """Immutable CLEARED records, newest first."""
        return list(self._history)

    def records(self) -> list[AlarmRecord]:
        """All records: live first, then history."""
        return self.live() + self.history()

    def get(self, cause: str) -> AlarmRecord | None:
        """Current live record for a cause, or ``None``."""
        return self._live.get(str(cause))
