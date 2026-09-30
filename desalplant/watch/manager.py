"""Graded alarm manager: ingestion, grading, chatter, acknowledge and clear."""

import json
import threading
from typing import Any, Callable, Iterable

from ..audit.event import Event
from ..audit.recorder import Recorder
from ..clock import LogicalClock
from ..store import keys
from ..store.codec import canonical
from ..store.store import Store
from .errors import (
    ClearConditionsUnmetError,
    IncidentNotAcknowledgedError,
    StaleTickError,
    UnknownIncidentError,
)
from .incident import Incident
from .policy import Policy
from .report import Report
from .tier import Tier
from .track import Track

ShutdownHook = Callable[[Incident], None]


class WatchManager:
    """Collects limit reports and grades them into notices and shutdowns.

    Ordering rules:

    * ticks are processed strictly in ascending order per cause -- a report
      older than an already processed tick is refused;
    * one tick is evaluated once, even when several sources report the same
      cause concurrently, so a cause can never raise or escalate twice for
      one tick;
    * identical (tick, cause, source, token) reports are absorbed, and so are
      reports arriving for a tick that has already been graded.
    """

    def __init__(
        self,
        store: Store,
        recorder: Recorder,
        clock: LogicalClock,
        policies: Iterable[Policy] = (),
        default_policy: Policy | None = None,
        on_shutdown: ShutdownHook | None = None,
    ) -> None:
        self._store = store
        self._recorder = recorder
        self._clock = clock
        self._lock = threading.RLock()
        self._policies: dict[str, Policy] = {}
        for policy in policies:
            self._policies[policy.cause] = policy
        self._default_policy = default_policy
        self._on_shutdown = on_shutdown
        self._tracks: dict[str, Track] = {}
        self._load()

    # ------------------------------------------------------------ policies

    def policy_for(self, cause: str) -> Policy:
        """Configured policy for a cause, falling back to the default."""
        policy = self._policies.get(cause)
        if policy is None:
            if self._default_policy is not None:
                template = self._default_policy
                policy = Policy(
                    cause=cause,
                    shutdown_sources=template.shutdown_sources,
                    shutdown_excess=template.shutdown_excess,
                    shutdown_after_ticks=template.shutdown_after_ticks,
                    clear_after_good_ticks=template.clear_after_good_ticks,
                    chatter_window_ticks=template.chatter_window_ticks,
                    notice_after_ticks=template.notice_after_ticks,
                )
            else:
                policy = Policy(cause=cause)
        return policy

    def policies(self) -> tuple[Policy, ...]:
        """All configured grading policies."""
        return tuple(sorted(self._policies.values(), key=lambda policy: policy.cause))

    def next_tick(self) -> int:
        """Tick for an internally generated sweep: past every processed tick."""
        highest = max((track.high_water for track in self._tracks.values()), default=-1)
        return max(highest + 1, self._clock.tick)

    # ----------------------------------------------------------- ingestion

    def ingest(self, report: Report, finalize: bool = False) -> dict[str, Any]:
        """Buffer one report; ``finalize`` grades the tick it belongs to."""
        return self.ingest_many([report], finalize=finalize)

    def ingest_tick(self, reports: Iterable[Report]) -> dict[str, Any]:
        """Buffer concurrent reports and grade their tick once, atomically."""
        return self.ingest_many(list(reports), finalize=True)

    def ingest_batch(self, reports: Iterable[Report]) -> dict[str, Any]:
        """Grade a mixed-cause, possibly multi-tick batch atomically.

        Causes are graded in alphabetical order and ticks in ascending order,
        so the audit sequence never depends on the order the producers sent
        their reports.
        """
        reports = list(reports)
        causes = sorted({report.cause for report in reports})
        with self._lock:
            result = self.ingest_many(reports, finalize=False)
            events: list[Event] = []
            for cause in causes:
                events.extend(self.advance(cause))
            result["events"] = [self._event_dict(event) for event in events]
            result["incidents"] = []
            for cause in causes:
                incident = self._tracks[cause].open_incident()
                if incident is not None:
                    result["incidents"].append(self._view(incident))
        return result

    def ingest_many(
        self, reports: Iterable[Report], finalize: bool = False
    ) -> dict[str, Any]:
        """Buffer a batch of (possibly concurrent, multi-source) reports.

        Reports for the same tick and cause are merged into one open batch. A
        batch is graded when ``finalize`` is set (after every tick in the
        batch, in ascending order), or later via :meth:`advance` / another
        finalizing call. Retried reports and reports for an already graded
        tick are absorbed, so no matter how many concurrent sources report,
        a cause raises or escalates at most once per tick.
        """
        with self._lock:
            accepted = 0
            duplicates = 0
            changed: set[str] = set()
            explicit: set[str] = set()
            for report in reports:
                track = self._tracks.setdefault(report.cause, Track(report.cause))
                if report.tick < track.high_water:
                    raise StaleTickError(
                        f"report for {report.cause} at tick {report.tick} is "
                        f"older than processed tick {track.high_water}"
                    )
                if report.dedupe_key() in track.seen or track.already_graded(report.tick):
                    track.seen.add(report.dedupe_key())
                    duplicates += 1
                    continue
                if track.pending_tick is not None and report.tick < track.pending_tick:
                    raise StaleTickError(
                        f"report for {report.cause} at tick {report.tick} "
                        f"joins an open batch at tick {track.pending_tick}"
                    )
                track.seen.add(report.dedupe_key())
                track.pending_tick = max(
                    report.tick, track.pending_tick if track.pending_tick is not None else -1
                )
                track.pending.append(report)
                accepted += 1
                changed.add(report.cause)
                if finalize:
                    explicit.add(report.cause)

            events: list[Event] = []
            for cause in sorted(explicit):
                track = self._tracks[cause]
                if track.pending:
                    events.extend(self._finalize(cause, max(track.pending_ticks())))
            for cause in changed:
                self._persist(cause)
            incidents = []
            for cause in sorted(changed):
                incident = self._tracks[cause].open_incident()
                if incident is not None:
                    incidents.append(self._view(incident))
            return {
                "accepted": accepted,
                "duplicates": duplicates,
                "events": [self._event_dict(event) for event in events],
                "incidents": incidents,
            }

    def advance(self, *causes: str) -> list[Event]:
        """Finalize every buffered tick of the given (or every) cause."""
        with self._lock:
            targets = causes or tuple(self._tracks)
            events: list[Event] = []
            for cause in sorted(targets):
                track = self._tracks.get(cause)
                if track is not None and track.pending:
                    events.extend(self._finalize(cause, max(track.pending_ticks())))
                    self._persist(cause)
            return events

    def _finalize(self, cause: str, ceiling: int) -> list[Event]:
        """Evaluate every buffered tick of a cause up to ``ceiling``, in order."""
        track = self._tracks[cause]
        policy = self.policy_for(cause)
        events: list[Event] = []
        ticks = sorted({report.tick for report in track.pending if report.tick <= ceiling})
        for tick in ticks:
            reports = track.take_pending(tick)
            if not reports:
                continue
            sources, excess = Track.merge(reports)
            events.extend(self._evaluate(track, policy, tick, sources, excess))
        return events

    def _evaluate(
        self,
        track: Track,
        policy: Policy,
        tick: int,
        sources: tuple[str, ...],
        excess: float,
    ) -> list[Event]:
        """Grade one merged observation for one cause at one tick."""
        track.high_water = tick
        incident = track.open_incident()
        if excess > 0.0:
            return self._on_breach(track, policy, tick, sources, excess, incident)
        return self._on_good(track, policy, tick, incident)

    # ---------------------------------------------------------- transitions

    def _on_breach(
        self,
        track: Track,
        policy: Policy,
        tick: int,
        sources: tuple[str, ...],
        excess: float,
        incident: Incident | None,
    ) -> list[Event]:
        """Process one breach tick against whatever incident is open."""
        events: list[Event] = []
        gap = 0
        if incident is not None and incident.acknowledged:
            # Frozen history: the acknowledged record is immutable. Track
            # counters still follow the data so an explicit resolve can test
            # the clear condition, but the incident is never mutated, never
            # escalated again and never silently removed.
            track.good_run = 0
            track.bad_run += 1
            return events

        if incident is not None:
            # gap = healthy ticks between the last breach and this one, which
            # works whether or not they were explicitly reported.
            gap = max(0, tick - incident.last_breach_tick - 1)
            if 1 <= gap <= policy.chatter_window_ticks:
                # A flap back inside the chatter window is one incident and
                # one additional chatter count, not a new alarm.
                incident.mark_chatter(tick)
                events.append(
                    self._log(
                        "alarm.chatter", policy, incident, tick,
                        sources=sources, excess=excess,
                    )
                )
                incident.bad_run = 1
            elif gap > policy.chatter_window_ticks:
                # The cause returned after the window: a distinct episode. The
                # prior unacknowledged incident is separated, never deleted.
                incident.separate(tick)
                events.append(
                    self._log(
                        "alarm.separate", policy, incident, tick,
                        sources=sources, excess=excess,
                    )
                )
                incident = None

        if incident is not None:
            was_chatter = bool(events) and events[-1].kind == "alarm.chatter"
            incident.touch(tick, sources, excess)
            if was_chatter:
                incident.bad_run = 1
            elif gap == 0:
                incident.bad_run += 1
            track.bad_run = incident.bad_run
            track.good_run = 0
            if incident.tier < Tier.SHUTDOWN and policy.requires_shutdown(
                sources, excess, incident.bad_run
            ):
                incident.escalate(tick)
                events.append(
                    self._log(
                        "alarm.shutdown", policy, incident, tick,
                        sources=sources, excess=excess,
                    )
                )
                if self._on_shutdown is not None:
                    self._on_shutdown(incident)
            return events

        # Fresh episode.
        track.bad_run = 1
        track.good_run = 0
        if not policy.notice_due(track.bad_run):
            return events
        incident = Incident.raise_new(policy, tick, sources, excess)
        track.incidents.append(incident)
        events.append(
            self._log("alarm.raise", policy, incident, tick, sources=sources, excess=excess)
        )
        if policy.requires_shutdown(sources, excess, incident.bad_run):
            incident.escalate(tick)
            events.append(
                self._log(
                    "alarm.shutdown", policy, incident, tick,
                    sources=sources, excess=excess,
                )
            )
            if self._on_shutdown is not None:
                self._on_shutdown(incident)
        return events

    def _on_good(
        self, track: Track, policy: Policy, tick: int, incident: Incident | None
    ) -> list[Event]:
        """Process one healthy tick; auto-clear only an unacknowledged alarm."""
        events: list[Event] = []
        track.bad_run = 0
        if incident is None:
            track.good_run = 0
            return events
        track.good_run += 1
        if incident.acknowledged:
            # Historical record: only an explicit resolve may close it.
            return events
        if track.good_run >= policy.clear_after_good_ticks:
            incident.clear(tick)
            track.good_run = 0
            events.append(self._log("alarm.clear", policy, incident, tick))
        return events

    # ------------------------------------------------------ acknowledgement

    def acknowledge(self, incident_id: str, tick: int | None = None) -> dict[str, Any]:
        """Mark an open alarm as acknowledged; the record then is immutable."""
        with self._lock:
            incident, track = self._require_incident(incident_id)
            stamp = self._clock.tick if tick is None else tick
            changed = incident.acknowledge(stamp)
            event = None
            if changed:
                event = self._log(
                    "alarm.ack", self.policy_for(incident.cause), incident, stamp
                )
            self._persist(incident.cause)
            view = self._view(incident)
            view["event"] = self._event_dict(event) if event is not None else None
            return view

    def resolve(self, incident_id: str, tick: int | None = None) -> dict[str, Any]:
        """Resolve an acknowledged alarm once its clear conditions hold."""
        with self._lock:
            incident, track = self._require_incident(incident_id)
            stamp = self._clock.tick if tick is None else tick
            if not incident.acknowledged:
                raise IncidentNotAcknowledgedError(
                    f"incident {incident_id} for {incident.cause} is not acknowledged"
                )
            if not incident.is_open:
                raise ClearConditionsUnmetError(
                    f"incident {incident_id} for {incident.cause} is already {incident.state}"
                )
            policy = self.policy_for(incident.cause)
            if track.bad_run > 0 or track.good_run < policy.clear_after_good_ticks:
                raise ClearConditionsUnmetError(
                    f"incident {incident_id} for {incident.cause} needs "
                    f"{policy.clear_after_good_ticks} consecutive good ticks, "
                    f"has {track.good_run}"
                )
            incident.resolve(stamp)
            track.good_run = 0
            event = self._log("alarm.resolve", policy, incident, stamp)
            self._persist(incident.cause)
            view = self._view(incident)
            view["event"] = self._event_dict(event)
            return view

    # --------------------------------------------------------------- views

    def active_incidents(self) -> list[dict[str, Any]]:
        """All still-open incidents across every cause."""
        with self._lock:
            views = []
            for cause in sorted(self._tracks):
                incident = self._tracks[cause].open_incident()
                if incident is not None:
                    views.append(self._view(incident))
            return views

    def history(self, cause: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Closed and open incidents, newest first, optionally for one cause."""
        with self._lock:
            causes = [cause] if cause is not None else sorted(self._tracks)
            incidents: list[Incident] = []
            for name in causes:
                track = self._tracks.get(name)
                if track is not None:
                    incidents.extend(track.incidents)
            incidents.sort(key=lambda incident: incident.raised_tick, reverse=True)
            if limit > 0:
                incidents = incidents[:limit]
            return [self._view(incident) for incident in incidents]

    def incident_view(self, incident_id: str) -> dict[str, Any]:
        """One incident with its grading basis and clear condition."""
        with self._lock:
            incident, _ = self._require_incident(incident_id)
            return self._view(incident)

    def _view(self, incident: Incident) -> dict[str, Any]:
        policy = self.policy_for(incident.cause)
        view = incident.to_dict()
        view["tier_basis"] = policy.basis()
        view["clear_condition"] = incident.clear_condition(policy)
        return view

    def _require_incident(self, incident_id: str) -> tuple[Incident, Track]:
        for track in self._tracks.values():
            incident = track.find(incident_id)
            if incident is not None:
                return incident, track
        raise UnknownIncidentError(f"no incident with id {incident_id}")

    # --------------------------------------------------------------- audit

    def _log(
        self,
        kind: str,
        policy: Policy,
        incident: Incident,
        tick: int,
        sources: tuple[str, ...] = (),
        excess: float = 0.0,
    ) -> Event:
        detail = {
            "incident_id": incident.id,
            "cause": incident.cause,
            "tick": tick,
            "tier": incident.tier.label(),
            "state": incident.state,
            "chatter_count": incident.chatter_count,
            "bad_run": incident.bad_run,
            "max_excess": incident.max_excess,
            "sources": list(sources) if sources else list(incident.sources),
            "excess": excess,
            "basis": policy.basis(),
            "clear_condition": incident.clear_condition(policy),
        }
        return self._recorder.log(kind, canonical(detail), tick=tick)

    @staticmethod
    def _event_dict(event: Event) -> dict[str, Any]:
        try:
            detail: Any = json.loads(event.detail)
        except (ValueError, TypeError):
            detail = event.detail
        return {
            "sequence": event.sequence,
            "kind": event.kind,
            "tick": event.tick,
            "detail": detail,
        }

    # --------------------------------------------------------- persistence

    def _persist(self, cause: str) -> None:
        track = self._tracks.get(cause)
        if track is None:
            return
        self._store.put(keys.watch_track_key(cause), track.to_dict())

    def _load(self) -> None:
        for key in self._store.keys(keys.WATCH_TRACK_PREFIX):
            cause = key[len(keys.WATCH_TRACK_PREFIX):]
            self._tracks[cause] = Track.from_dict(cause, self._store.get(key))
