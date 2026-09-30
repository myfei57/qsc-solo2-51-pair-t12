"""Read-only graded alarm console handlers."""

from .http import Request, Response, json_response


def incidents(console, request: Request) -> Response:
    """Open graded incidents, or incident history when ``history=1`` is set."""
    cause = request.param("cause")
    limit = request.integer("limit", 100)
    if request.param("history") == "1":
        return json_response(
            {
                "incidents": console.plant.watch_history(cause=cause, limit=limit),
            }
        )
    return json_response(
        {
            "incidents": console.plant.watch_active(),
            "count": len(console.plant.watch_active()),
        }
    )


def incident(console, request: Request) -> Response:
    """One incident with grading basis, chatter count and clear condition."""
    incident_id = request.param("id", "")
    return json_response(console.plant.watch_incident(incident_id))


def policies(console, request: Request) -> Response:
    """Configured grading basis for every monitored cause."""
    return json_response({"policies": console.plant.watch_policies()})


ROUTE_TABLE = (
    ("/api/watch/incidents", incidents),
    ("/api/watch/incident", incident),
    ("/api/watch/policies", policies),
)
