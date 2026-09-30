"""Mutating graded alarm console operations."""

from .http import Request, Response, json_response


def report(console, request: Request) -> Response:
    """Ingest one reported observation, or a concurrent same-tick batch."""
    body = request.body_object_or_list()
    result = console.plant.watch_report(body)
    return json_response(result)


def acknowledge(console, request: Request) -> Response:
    """Acknowledge an open incident, freezing it as immutable history."""
    body = request.body()
    incident_id = str(body.get("incident_id", "")).strip()
    return json_response(console.plant.watch_acknowledge(incident_id))


def resolve(console, request: Request) -> Response:
    """Resolve an acknowledged incident once clear conditions hold."""
    body = request.body()
    incident_id = str(body.get("incident_id", "")).strip()
    return json_response(console.plant.watch_resolve(incident_id))


def sweep(console, request: Request) -> Response:
    """Grade the current component readings in one concurrent sweep."""
    request.body()
    return json_response(console.plant.watch_sweep())


ROUTE_TABLE = (
    ("POST", "/api/watch/report", report),
    ("POST", "/api/watch/incidents/ack", acknowledge),
    ("POST", "/api/watch/incidents/resolve", resolve),
    ("POST", "/api/watch/sweep", sweep),
)
