"""Graded alarm manager: grading, chatter, idempotency, acknowledgement."""

import json

import pytest

from desalplant.audit.recorder import Recorder
from desalplant.clock import LogicalClock
from desalplant.store.store import Store
from desalplant.watch import Policy, Report, Tier, WatchManager
from desalplant.watch.errors import (
    ClearConditionsUnmetError,
    IncidentNotAcknowledgedError,
    StaleTickError,
    UnknownIncidentError,
)


def manager(tmp_path, policies=(), **kwargs):
    store = Store(str(tmp_path / "watch.json"))
    clock = LogicalClock()
    recorder = Recorder(store, clock)
    return WatchManager(store, recorder, clock, policies=policies, **kwargs), recorder


def policy(cause="level", **overrides):
    defaults = dict(
        shutdown_sources=frozenset(),
        shutdown_excess=5.0,
        shutdown_after_ticks=4,
        clear_after_good_ticks=3,
        chatter_window_ticks=2,
    )
    defaults.update(overrides)
    return Policy(cause=cause, **defaults)


def put(watch, tick, source="gauge", excess=1.0, cause="level"):
    """Buffer one report and grade its tick immediately."""
    return watch.ingest_tick([Report(tick, cause, source, excess)])


def test_first_breach_raises_a_notice(tmp_path):
    watch, _ = manager(tmp_path, [policy()])
    result = put(watch, 0)
    assert result["accepted"] == 1
    assert [event["kind"] for event in result["events"]] == ["alarm.raise"]
    incident = result["incidents"][0]
    assert incident["tier"] == Tier.NOTICE.label()
    assert incident["state"] == "open"
    assert incident["chatter_count"] == 0


def test_excess_threshold_escalates_to_shutdown(tmp_path):
    watch, recorder = manager(tmp_path, [policy()])
    put(watch, 0)
    result = put(watch, 1, excess=5.0)
    assert [event["kind"] for event in result["events"]] == ["alarm.shutdown"]
    assert result["incidents"][0]["tier"] == Tier.SHUTDOWN.label()
    assert recorder.latest("alarm.shutdown") is not None


def test_shutdown_source_escalates_on_the_first_tick(tmp_path):
    watch, _ = manager(
        tmp_path, [policy(shutdown_sources=frozenset({"safety-plc"}))]
    )
    result = put(watch, 0, source="safety-plc", excess=0.1)
    kinds = [event["kind"] for event in result["events"]]
    assert kinds == ["alarm.raise", "alarm.shutdown"]


def test_duration_escalates_after_the_configured_run(tmp_path):
    watch, _ = manager(tmp_path, [policy(shutdown_excess=None)])
    first = put(watch, 0)
    assert [event["kind"] for event in first["events"]] == ["alarm.raise"]
    for tick in (1, 2):
        assert put(watch, tick)["events"] == []
    assert [event["kind"] for event in put(watch, 3)["events"]] == ["alarm.shutdown"]


def test_concurrent_sources_same_tick_raise_once_and_merge(tmp_path):
    watch, _ = manager(tmp_path, [policy()])
    result = watch.ingest_tick(
        [
            Report(0, "level", "gauge-a", 1.0),
            Report(0, "level", "gauge-b", 2.0),
        ]
    )
    assert result["accepted"] == 2
    assert [event["kind"] for event in result["events"]] == ["alarm.raise"]
    incident = result["incidents"][0]
    assert incident["sources"] == ["gauge-a", "gauge-b"]
    assert incident["max_excess"] == 2.0


def test_concurrent_sources_buffered_across_separate_calls(tmp_path):
    watch, _ = manager(tmp_path, [policy(shutdown_excess=None)])
    # Both sources buffer onto the still-open tick without grading yet.
    first = watch.ingest_many([Report(0, "level", "gauge-a", 1.0)])
    second = watch.ingest_many([Report(0, "level", "gauge-b", 1.0)])
    assert first["events"] == [] and second["events"] == []
    graded = watch.ingest_tick([Report(0, "level", "gauge-c", 1.0)])
    assert [event["kind"] for event in graded["events"]] == ["alarm.raise"]
    assert graded["incidents"][0]["sources"] == ["gauge-a", "gauge-b", "gauge-c"]


def test_retried_report_after_grading_is_absorbed_idempotently(tmp_path):
    watch, _ = manager(tmp_path, [policy()])
    put(watch, 0, excess=1.0)
    retry = watch.ingest_tick([Report(0, "level", "gauge", 9.0)])
    assert retry["accepted"] == 0
    assert retry["duplicates"] == 1
    assert retry["events"] == []
    # a late source arriving after the tick was graded cannot escalate twice
    late = watch.ingest_tick([Report(0, "level", "safety-plc", 9.0)])
    assert late["accepted"] == 0
    assert late["duplicates"] == 1
    assert late["events"] == []
    assert watch.active_incidents()[0]["tier"] == "notice"


def test_token_dedupes_unfinalized_concurrent_batches(tmp_path):
    watch, _ = manager(tmp_path, [policy()])
    first = watch.ingest_many(
        [Report(5, "level", "gauge", 1.0, token="t-1")], finalize=False
    )
    second = watch.ingest_many(
        [Report(5, "level", "gauge", 1.0, token="t-1")], finalize=False
    )
    assert first["accepted"] == 1
    assert second["accepted"] == 0 and second["duplicates"] == 1


def test_stale_tick_is_refused(tmp_path):
    watch, _ = manager(tmp_path, [policy()])
    put(watch, 2)
    with pytest.raises(StaleTickError):
        put(watch, 1)


def test_newer_tick_closes_the_open_tick_in_order(tmp_path):
    watch, _ = manager(tmp_path, [policy()])
    watch.ingest_many([Report(0, "level", "gauge", 1.0)])
    # arriving tick 1 grades tick 0 first, then tick 1
    result = watch.ingest_tick([Report(1, "level", "gauge", 1.0)])
    kinds = [event["kind"] for event in result["events"]]
    assert kinds == ["alarm.raise"]


def test_chatter_flap_is_compressed_and_counted(tmp_path):
    watch, _ = manager(tmp_path, [policy()])
    incident_id = put(watch, 0)["incidents"][0]["id"]
    put(watch, 1, excess=0.0)
    put(watch, 2, excess=0.0)
    flap = put(watch, 3, excess=1.0)
    assert [event["kind"] for event in flap["events"]] == ["alarm.chatter"]
    assert flap["incidents"][0]["id"] == incident_id
    assert flap["incidents"][0]["chatter_count"] == 1


def test_flap_after_the_window_does_not_count_as_chatter(tmp_path):
    watch, _ = manager(tmp_path, [policy()])
    put(watch, 0)
    put(watch, 1, excess=0.0)
    put(watch, 2, excess=0.0)
    cleared = put(watch, 3, excess=0.0)
    assert [event["kind"] for event in cleared["events"]] == ["alarm.clear"]
    rebreach = put(watch, 4)
    assert [event["kind"] for event in rebreach["events"]] == ["alarm.raise"]
    assert rebreach["incidents"][0]["chatter_count"] == 0


def test_return_after_the_chatter_window_starts_a_new_episode(tmp_path):
    watch, _ = manager(tmp_path, [policy()])
    first = put(watch, 0)["incidents"][0]
    put(watch, 1, excess=0.0)
    put(watch, 2, excess=0.0)
    # breach at tick 10 leaves nine good ticks behind: outside the window
    reopened = put(watch, 10)
    kinds = [event["kind"] for event in reopened["events"]]
    assert kinds == ["alarm.separate", "alarm.raise"]
    history = watch.history("level")
    assert history[0]["state"] == "open"
    assert history[0]["id"] != first["id"]
    assert history[1]["id"] == first["id"]
    assert history[1]["state"] == "separated"


def test_consecutive_breach_ticks_do_not_count_as_chatter(tmp_path):
    watch, _ = manager(tmp_path, [policy()])
    put(watch, 0)
    put(watch, 1)
    active = watch.active_incidents()[0]
    assert active["chatter_count"] == 0
    assert active["bad_run"] == 2


def test_unacknowledged_incident_auto_clears_after_good_run(tmp_path):
    watch, _ = manager(tmp_path, [policy()])
    put(watch, 0)
    for tick in (1, 2):
        assert put(watch, tick, excess=0.0)["events"] == []
    cleared = put(watch, 3, excess=0.0)
    assert [event["kind"] for event in cleared["events"]] == ["alarm.clear"]
    assert watch.active_incidents() == []


def test_acknowledged_incident_is_immutable_history(tmp_path):
    watch, _ = manager(tmp_path, [policy()])
    incident_id = put(watch, 0)["incidents"][0]["id"]
    watch.acknowledge(incident_id)
    # more breach data must not mutate, re-escalate or remove the record
    for tick in (1, 2, 3):
        assert put(watch, tick, excess=9.0)["events"] == []
    view = watch.incident_view(incident_id)
    assert view["acknowledged"] is True
    assert view["state"] == "open"
    assert view["max_excess"] == 1.0
    assert view["chatter_count"] == 0
    # good data never silently clears it either
    for tick in (4, 5, 6, 7):
        put(watch, tick, excess=0.0)
    assert watch.incident_view(incident_id)["state"] == "open"


def test_acknowledge_is_idempotent(tmp_path):
    watch, _ = manager(tmp_path, [policy()])
    incident_id = put(watch, 0)["incidents"][0]["id"]
    first = watch.acknowledge(incident_id)
    second = watch.acknowledge(incident_id)
    assert first["event"]["kind"] == "alarm.ack"
    assert second["event"] is None


def test_resolve_requires_explicit_good_run(tmp_path):
    watch, _ = manager(tmp_path, [policy()])
    incident_id = put(watch, 0)["incidents"][0]["id"]
    watch.acknowledge(incident_id)
    with pytest.raises(ClearConditionsUnmetError):
        watch.resolve(incident_id)
    for tick in (1, 2, 3):
        put(watch, tick, excess=0.0)
    assert watch.resolve(incident_id)["state"] == "resolved"


def test_resolve_unacknowledged_is_refused(tmp_path):
    watch, _ = manager(tmp_path, [policy()])
    incident_id = put(watch, 0)["incidents"][0]["id"]
    with pytest.raises(IncidentNotAcknowledgedError):
        watch.resolve(incident_id)


def test_unknown_incident_is_rejected(tmp_path):
    watch, _ = manager(tmp_path, [policy()])
    with pytest.raises(UnknownIncidentError):
        watch.acknowledge("missing")
    with pytest.raises(UnknownIncidentError):
        watch.incident_view("missing")


def test_shutdown_hook_fires_once_per_incident(tmp_path):
    tripped = []
    watch, _ = manager(
        tmp_path, [policy()], on_shutdown=lambda incident: tripped.append(incident.id)
    )
    put(watch, 0, excess=9.0)
    put(watch, 1, excess=9.0)
    assert len(tripped) == 1


def test_audit_events_carry_basis_chatter_and_clear_condition(tmp_path):
    watch, recorder = manager(tmp_path, [policy()])
    put(watch, 0)
    event = recorder.latest("alarm.raise")
    detail = json.loads(event.detail)
    assert detail["cause"] == "level"
    assert detail["tier"] == "notice"
    assert detail["chatter_count"] == 0
    dimensions = {row["dimension"] for row in detail["basis"]}
    assert dimensions == {"source", "excess", "duration", "recovery"}
    assert detail["clear_condition"]["good_ticks"] == 3


def test_audit_sequence_is_monotonic_without_gaps(tmp_path):
    watch, recorder = manager(tmp_path, [policy()])
    put(watch, 0)
    put(watch, 1, excess=9.0)
    put(watch, 2, excess=0.0)
    put(watch, 3, excess=0.0)
    put(watch, 4, excess=0.0)
    events = recorder.events()
    sequences = [event.sequence for event in reversed(events)]
    assert sequences == list(range(1, len(sequences) + 1))


def test_state_survives_a_restart(tmp_path):
    path = str(tmp_path / "restart.json")
    store = Store(path)
    first = WatchManager(
        store, Recorder(store, LogicalClock()), LogicalClock(), policies=[policy()]
    )
    put(first, 0)
    incident_id = first.active_incidents()[0]["id"]

    reopened_store = Store(path)
    reopened = WatchManager(
        reopened_store,
        Recorder(reopened_store, LogicalClock()),
        LogicalClock(),
        policies=[policy()],
    )
    active = reopened.active_incidents()
    assert len(active) == 1 and active[0]["id"] == incident_id
    with pytest.raises(StaleTickError):
        put(reopened, 0)


def test_policy_rejects_a_window_as_long_as_the_clear_run():
    with pytest.raises(ValueError):
        Policy(cause="x", clear_after_good_ticks=2, chatter_window_ticks=2)


def test_escalation_never_downgrades(tmp_path):
    watch, _ = manager(tmp_path, [policy()])
    put(watch, 0, excess=9.0)
    incident = put(watch, 1, excess=0.1)["incidents"][0]
    assert incident["tier"] == Tier.SHUTDOWN.label()
