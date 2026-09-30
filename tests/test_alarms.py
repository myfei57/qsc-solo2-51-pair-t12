"""Graded alarm behaviour: grading, jitter, idempotency, history and audit."""

import pytest

from desalplant.alarms.evaluate import observations
from desalplant.alarms.policy import AlarmPolicy
from desalplant.alarms.record import AlarmState
from desalplant.alarms.registry import AlarmRegistry
from desalplant.audit.recorder import Recorder
from desalplant.clock import LogicalClock
from desalplant.console.plant import Plant
from desalplant.errors import AlarmImmutableError, UnknownKeyError
from desalplant.store.store import Store

from support import build, call, ok


def make_registry(policy=None):
    store = Store()
    clock = LogicalClock()
    recorder = Recorder(store, clock)
    registry = AlarmRegistry(store, clock, recorder, policy)
    return registry, store, clock, recorder


def raise_cause(registry, cause, magnitude=1.0, source="level", message="alarm", tick=None):
    registry.sweep([(cause, source, True, magnitude, message)])


def clear_cause(registry, cause, tick):
    registry.sweep([(cause, "level", False, 0.0, "")])


# --------------------------------------------------------------------- grading


def test_grading_escalates_with_magnitude():
    registry, _, _, _ = make_registry()
    raise_cause(registry, "pump.pressure.high", magnitude=0.20, source="pump")
    rec = registry.get("pump.pressure.high")
    assert rec.severity.label() == "warning"
    assert "overshoot 0.20" in rec.grading_basis

    registry2, _, _, _ = make_registry()
    raise_cause(registry2, "pump.pressure.high", magnitude=0.40, source="pump")
    rec2 = registry2.get("pump.pressure.high")
    assert rec2.severity.label() == "critical"


def test_grading_escalates_with_duration():
    policy = AlarmPolicy(medium_duration=3, long_duration=6, clear_delay=20, jitter_window=2)
    registry, _, clock, _ = make_registry(policy)
    raise_cause(registry, "tank.level.low", magnitude=0.20)
    rec = registry.get("tank.level.low")
    assert rec.severity.label() == "warning"  # magnitude 0.20

    # Persist past the medium duration: still warning (no downgrade).
    for _ in range(3):
        clock.advance()
        raise_cause(registry, "tank.level.low", magnitude=0.20)
    rec = registry.get("tank.level.low")
    assert rec.severity.label() == "warning"

    # Persist past the long duration: escalate to critical.
    for _ in range(3):
        clock.advance()
        raise_cause(registry, "tank.level.low", magnitude=0.20)
    rec = registry.get("tank.level.low")
    assert rec.severity.label() == "critical"
    assert rec.escalations >= 1
    assert "duration" in rec.grading_basis


def test_grading_never_downgrades_on_reevaluation():
    registry, _, clock, _ = make_registry()
    raise_cause(registry, "tank.level.low", magnitude=1.0)
    rec = registry.get("tank.level.low")
    assert rec.severity.label() == "critical"
    # A later observation with a smaller magnitude must not downgrade.
    clock.advance()
    raise_cause(registry, "tank.level.low", magnitude=0.05)
    rec = registry.get("tank.level.low")
    assert rec.severity.label() == "critical"


def test_source_floor_raises_severity():
    registry, _, _, _ = make_registry()
    # A pump cause with a tiny magnitude still grades at least warning.
    raise_cause(registry, "pump.stalled", magnitude=0.01, source="pump")
    rec = registry.get("pump.stalled")
    assert rec.severity.label() == "warning"
    assert "source pump floor" in rec.grading_basis


# --------------------------------------------------------------------- jitter


def test_jitter_collapses_a_flapping_cause_into_one_record():
    policy = AlarmPolicy(jitter_window=3, clear_delay=20)
    registry, _, clock, _ = make_registry(policy)
    raise_cause(registry, "tank.level.low", magnitude=1.0)
    assert len(registry.live()) == 1

    # Flap: absent one tick, present again within the jitter window.
    clock.advance()
    clear_cause(registry, "tank.level.low", clock.tick)
    clock.advance()
    raise_cause(registry, "tank.level.low", magnitude=1.0)
    rec = registry.get("tank.level.low")
    assert rec.jitter_count == 1
    assert len(registry.live()) == 1

    # Flap again.
    clock.advance()
    clear_cause(registry, "tank.level.low", clock.tick)
    clock.advance()
    raise_cause(registry, "tank.level.low", magnitude=1.0)
    rec = registry.get("tank.level.low")
    assert rec.jitter_count == 2
    assert len(registry.live()) == 1


def test_jitter_does_not_grow_for_a_persistent_condition():
    registry, _, clock, _ = make_registry()
    raise_cause(registry, "tank.level.low", magnitude=1.0)
    for _ in range(5):
        clock.advance()
        raise_cause(registry, "tank.level.low", magnitude=1.0)
    rec = registry.get("tank.level.low")
    assert rec.jitter_count == 0


def test_reevaluating_at_the_same_tick_does_not_double_count():
    registry, _, _, _ = make_registry()
    raise_cause(registry, "tank.level.low", magnitude=1.0)
    # Sweep the same tick again: no time passed, so no jitter or ageing.
    raise_cause(registry, "tank.level.low", magnitude=1.0)
    rec = registry.get("tank.level.low")
    assert rec.jitter_count == 0
    assert rec.duration(rec.last_tick) == 0


# --------------------------------------------------------------- idempotency


def test_multiple_sources_reporting_the_same_cause_open_one_record():
    registry, _, _, _ = make_registry()
    registry.sweep(
        [
            ("tank.level.low", "level", True, 1.0, "tank level low"),
            ("tank.level.low", "level", True, 1.0, "tank level low"),
        ]
    )
    assert len(registry.live()) == 1
    assert registry.get("tank.level.low").source == "level"


def test_repeated_raise_does_not_reescalate():
    policy = AlarmPolicy(medium_duration=2, long_duration=4, clear_delay=20)
    registry, _, clock, _ = make_registry(policy)
    raise_cause(registry, "tank.level.low", magnitude=0.20)
    for _ in range(4):
        clock.advance()
        raise_cause(registry, "tank.level.low", magnitude=0.20)
    rec = registry.get("tank.level.low")
    assert rec.escalations == 1  # only one escalation, not one per report


# ------------------------------------------------------------- acknowledgement


def test_acknowledged_alarm_is_immutable_history():
    policy = AlarmPolicy(jitter_window=2, clear_delay=2)
    registry, _, clock, _ = make_registry(policy)
    raise_cause(registry, "tank.level.low", magnitude=1.0)
    registry.acknowledge("tank.level.low", by="alice")
    rec = registry.get("tank.level.low")
    assert rec.state is AlarmState.ACKNOWLEDGED
    assert rec.acknowledged_by == "alice"

    # Subsequent normal data must not silently clear the acknowledged alarm.
    for _ in range(10):
        clock.advance()
        clear_cause(registry, "tank.level.low", clock.tick)
    rec = registry.get("tank.level.low")
    assert rec.state is AlarmState.ACKNOWLEDGED
    assert rec.acknowledged_by == "alice"


def test_acknowledge_is_idempotent():
    registry, _, _, _ = make_registry()
    raise_cause(registry, "tank.level.low", magnitude=1.0)
    first = registry.acknowledge("tank.level.low")
    second = registry.acknowledge("tank.level.low")
    assert first.acknowledged_at == second.acknowledged_at


def test_acknowledge_unknown_cause_is_refused():
    registry, _, _, _ = make_registry()
    with pytest.raises(UnknownKeyError):
        registry.acknowledge("tank.level.low")


def test_acknowledge_cleared_history_is_refused():
    policy = AlarmPolicy(jitter_window=1, clear_delay=1)
    registry, _, clock, _ = make_registry(policy)
    raise_cause(registry, "tank.level.low", magnitude=1.0)
    clock.advance()
    clear_cause(registry, "tank.level.low", clock.tick)
    assert registry.get("tank.level.low") is None
    with pytest.raises(AlarmImmutableError):
        registry.acknowledge("tank.level.low")


# ------------------------------------------------------------------- clearing


def test_unacknowledged_alarm_autoclears_under_explicit_condition():
    policy = AlarmPolicy(jitter_window=3, clear_delay=3)
    registry, _, clock, _ = make_registry(policy)
    raise_cause(registry, "tank.level.low", magnitude=1.0)
    for _ in range(3):
        clock.advance()
        clear_cause(registry, "tank.level.low", clock.tick)
    assert registry.get("tank.level.low") is None
    history = registry.history()
    assert len(history) == 1
    assert history[0].state is AlarmState.CLEARED
    assert history[0].clearing_condition == "condition normal for 3 consecutive ticks"


def test_acknowledged_alarm_requires_explicit_clear():
    policy = AlarmPolicy(jitter_window=2, clear_delay=2)
    registry, _, clock, _ = make_registry(policy)
    raise_cause(registry, "tank.level.low", magnitude=1.0)
    registry.acknowledge("tank.level.low")
    for _ in range(10):
        clock.advance()
        clear_cause(registry, "tank.level.low", clock.tick)
    assert registry.get("tank.level.low").state is AlarmState.ACKNOWLEDGED

    registry.clear("tank.level.low", reason="operator reviewed")
    assert registry.get("tank.level.low") is None
    assert registry.history()[0].clearing_condition == "operator reviewed"


def test_clear_is_idempotent():
    policy = AlarmPolicy(jitter_window=1, clear_delay=1)
    registry, _, clock, _ = make_registry(policy)
    raise_cause(registry, "tank.level.low", magnitude=1.0)
    clock.advance()
    clear_cause(registry, "tank.level.low", clock.tick)
    first = registry.clear("tank.level.low")
    second = registry.clear("tank.level.low")
    assert first.cleared_at == second.cleared_at


# --------------------------------------------------------------------- audit


def test_state_changes_are_audited_with_basis_jitter_and_condition():
    policy = AlarmPolicy(medium_duration=2, long_duration=4, clear_delay=20, jitter_window=2)
    registry, _, clock, recorder = make_registry(policy)
    raise_cause(registry, "tank.level.low", magnitude=0.20)
    for _ in range(4):
        clock.advance()
        raise_cause(registry, "tank.level.low", magnitude=0.20)
    registry.acknowledge("tank.level.low")
    registry.clear("tank.level.low", reason="operator reviewed")

    kinds = [event.kind for event in recorder.events()]
    assert "alarm.raised" in kinds
    assert "alarm.escalated" in kinds
    assert "alarm.acknowledged" in kinds
    assert "alarm.cleared" in kinds

    raised = recorder.latest("alarm.raised")
    assert "overshoot" in raised.detail
    cleared = recorder.latest("alarm.cleared")
    assert "operator reviewed" in cleared.detail
    assert "jitter=" in cleared.detail


def test_jitter_is_audited():
    policy = AlarmPolicy(jitter_window=2, clear_delay=20, jitter_audit_every=1)
    registry, _, clock, recorder = make_registry(policy)
    raise_cause(registry, "tank.level.low", magnitude=1.0)
    clock.advance()
    clear_cause(registry, "tank.level.low", clock.tick)
    clock.advance()
    raise_cause(registry, "tank.level.low", magnitude=1.0)
    assert recorder.latest("alarm.jitter") is not None
    assert "chattered 1 times" in recorder.latest("alarm.jitter").detail


# ------------------------------------------------------------- console API


def test_console_alarms_endpoint_returns_graded_records(console):
    alarms = ok(console, "GET", "/api/alarms")
    assert isinstance(alarms, list)
    low = next(a for a in alarms if a["cause"] == "tank.level.low")
    assert low["state"] == "active"
    assert low["severity"] == "critical"
    assert "grading_basis" in low


def test_console_acknowledge_and_clear(console):
    body = ok(console, "POST", "/api/alarms/acknowledge", {"cause": "tank.level.low", "by": "bob"})
    assert body["status"] == "acknowledged"
    assert body["alarm"]["state"] == "acknowledged"
    assert body["alarm"]["acknowledged_by"] == "bob"

    body = ok(console, "POST", "/api/alarms/clear", {"cause": "tank.level.low", "reason": "resolved"})
    assert body["status"] == "cleared"
    assert body["alarm"]["state"] == "cleared"
    assert body["alarm"]["clearing_condition"] == "resolved"


def test_console_alarms_history_flag(console):
    ok(console, "POST", "/api/alarms/acknowledge", {"cause": "tank.level.low"})
    ok(console, "POST", "/api/alarms/clear", {"cause": "tank.level.low"})
    live = ok(console, "GET", "/api/alarms")
    history = ok(console, "GET", "/api/alarms?history=1")
    assert all(a["state"] != "cleared" for a in live)
    assert any(a["state"] == "cleared" for a in history)


def test_console_acknowledge_unknown_cause_is_refused(console):
    response = call(console, "POST", "/api/alarms/acknowledge", {"cause": "nope"})
    assert response.status == 404


def test_console_acknowledge_requires_a_cause(console):
    response = call(console, "POST", "/api/alarms/acknowledge", {})
    assert response.status == 400


def test_console_events_carry_alarm_audit_detail(console):
    ok(console, "POST", "/api/alarms/acknowledge", {"cause": "tank.level.low"})
    body = ok(console, "GET", "/api/events")
    kinds = {event["kind"] for event in body["events"]}
    assert "alarm.raised" in kinds
    assert "alarm.acknowledged" in kinds
    raised = next(e for e in body["events"] if e["kind"] == "alarm.raised")
    assert "overshoot" in raised["detail"]


# ------------------------------------------------------------- persistence


def test_alarm_records_survive_a_restart(config):
    services = build(config)
    console = services.console
    ok(console, "POST", "/api/alarms/acknowledge", {"cause": "tank.level.low", "by": "bob"})

    # Rebuild from the same persisted store.
    restored = build(config)
    alarms = ok(restored.console, "GET", "/api/alarms")
    low = next(a for a in alarms if a["cause"] == "tank.level.low")
    assert low["state"] == "acknowledged"
    assert low["acknowledged_by"] == "bob"
