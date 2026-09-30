"""Console routes for graded alarms."""

from support import call, ok, refused


def test_watch_policies_list_the_grading_basis(console):
    body = ok(console, "GET", "/api/watch/policies")
    causes = {policy["cause"] for policy in body["policies"]}
    assert {
        "tank_level_high",
        "tank_level_low",
        "chemical_tank_level_low",
        "pump_pressure_high",
        "pump_supply_stalled",
    } <= causes
    first = body["policies"][0]
    assert {"shutdown_sources", "shutdown_excess", "shutdown_after_ticks",
            "clear_after_good_ticks", "chatter_window_ticks",
            "entry_tier", "shutdown_tier"} <= set(first)


def test_report_grades_notice_then_shutdown(console):
    assert ok(console, "GET", "/api/watch/incidents")["count"] == 0
    raised = ok(
        console,
        "POST",
        "/api/watch/report",
        {"tick": 1, "cause": "tank_level_high", "source": "gauge", "excess": 1.0},
    )
    assert [event["kind"] for event in raised["events"]] == ["alarm.raise"]
    incident_id = raised["incidents"][0]["id"]
    assert raised["incidents"][0]["tier"] == "notice"

    escalated = ok(
        console,
        "POST",
        "/api/watch/report",
        {"tick": 2, "cause": "tank_level_high", "source": "gauge", "excess": 12.0},
    )
    assert [event["kind"] for event in escalated["events"]] == ["alarm.shutdown"]

    listing = ok(console, "GET", "/api/watch/incidents")["incidents"]
    assert listing[0]["id"] == incident_id
    assert listing[0]["tier"] == "shutdown"
    assert len(listing[0]["tier_basis"]) == 4
    assert listing[0]["clear_condition"]["good_ticks"] == 3
    assert listing[0]["clear_condition"]["requires"] == "consecutive_good_ticks"


def test_concurrent_same_tick_reports_grade_once(console):
    first = ok(
        console,
        "POST",
        "/api/watch/report",
        {"tick": 0, "cause": "tank_level_low", "source": "gauge-a", "excess": 1.0},
    )
    retry = ok(
        console,
        "POST",
        "/api/watch/report",
        {"tick": 0, "cause": "tank_level_low", "source": "gauge-a", "excess": 1.0},
    )
    assert retry["duplicates"] == 1
    assert retry["events"] == []
    assert first["incidents"][0]["sources"] == ["gauge-a"]


def test_shutdown_source_trips_a_running_line(console):
    ok(console, "POST", "/api/start", {})
    body = ok(
        console,
        "POST",
        "/api/watch/report",
        {"tick": 5, "cause": "tank_level_low", "source": "level-switch", "excess": 2.0},
    )
    kinds = [event["kind"] for event in body["events"]]
    assert kinds == ["alarm.raise", "alarm.shutdown"]
    assert ok(console, "GET", "/api/status")["state"] == "idle"
    kinds = [event["kind"] for event in ok(console, "GET", "/api/events")["events"]]
    assert "trip" in kinds


def test_stale_tick_is_a_conflict(console):
    ok(
        console,
        "POST",
        "/api/watch/report",
        {"tick": 3, "cause": "tank_level_high", "source": "gauge", "excess": 1.0},
    )
    response = refused(
        console,
        "POST",
        "/api/watch/report",
        {"tick": 1, "cause": "tank_level_high", "source": "gauge", "excess": 1.0},
    )
    assert response.status == 409


def test_acknowledged_alarm_resolves_only_after_good_ticks(console):
    raised = ok(
        console,
        "POST",
        "/api/watch/report",
        {"tick": 1, "cause": "tank_level_high", "source": "gauge", "excess": 1.0},
    )
    incident_id = raised["incidents"][0]["id"]
    acknowledged = ok(
        console, "POST", "/api/watch/incidents/ack", {"incident_id": incident_id}
    )
    assert acknowledged["acknowledged"] is True

    # breach data cannot remove or change the acknowledged record
    ok(
        console,
        "POST",
        "/api/watch/report",
        {"tick": 2, "cause": "tank_level_high", "source": "gauge", "excess": 12.0},
    )
    view = ok(console, "GET", f"/api/watch/incident?id={incident_id}")
    assert view["state"] == "open" and view["tier"] == "notice"
    assert view["chatter_count"] == 0

    # resolve while still breaching is refused
    assert (
        refused(console, "POST", "/api/watch/incidents/resolve",
                {"incident_id": incident_id}).status == 409
    )
    # three consecutive good ticks allow the explicit resolve
    for tick in (3, 4, 5):
        ok(
            console,
            "POST",
            "/api/watch/report",
            {"tick": tick, "cause": "tank_level_high", "source": "gauge", "excess": 0.0},
        )
    resolved = ok(
        console, "POST", "/api/watch/incidents/resolve", {"incident_id": incident_id}
    )
    assert resolved["state"] == "resolved"


def test_unknown_incident_is_not_found(console):
    response = refused(
        console, "POST", "/api/watch/incidents/ack", {"incident_id": "nope"}
    )
    assert response.status == 404


def test_history_reports_cleared_and_open_incidents(console):
    # chem tank needs four good ticks to clear
    ok(
        console,
        "POST",
        "/api/watch/report",
        {"tick": 0, "cause": "chemical_tank_level_low", "source": "doser", "excess": 1.0},
    )
    for tick in (1, 2, 3, 4):
        ok(
            console,
            "POST",
            "/api/watch/report",
            {"tick": tick, "cause": "chemical_tank_level_low", "source": "doser", "excess": 0.0},
        )
    body = ok(console, "GET", "/api/watch/incidents?history=1&cause=chemical_tank_level_low")
    assert len(body["incidents"]) == 1
    assert body["incidents"][0]["state"] == "cleared"
    assert ok(console, "GET", "/api/watch/incidents")["count"] == 0


def test_sweep_grades_real_component_readings(console, plant):
    ok(console, "POST", "/api/start", {})
    plant.produce()
    body = ok(console, "POST", "/api/watch/sweep", {})
    assert body["accepted"] == 5
    assert body["events"] == []
    plant.level.update(95.0)
    breached = ok(console, "POST", "/api/watch/sweep", {})
    assert [event["kind"] for event in breached["events"]] == ["alarm.raise"]


def test_graded_alarm_events_appear_in_the_audit_feed(console):
    ok(
        console,
        "POST",
        "/api/watch/report",
        {"tick": 1, "cause": "tank_level_high", "source": "gauge", "excess": 1.0},
    )
    events = ok(console, "GET", "/api/events")["events"]
    raise_event = next(event for event in events if event["kind"] == "alarm.raise")
    assert raise_event["tick"] == 1
    assert "basis" in raise_event["detail"]
    assert "clear_condition" in raise_event["detail"]
