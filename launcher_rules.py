"""Shared launcher UI/action rules.

Keep these predicates pure so the backend, frontend port, and tests can agree
on which actions are safe without touching subprocess state.
"""

RUNNING_PROCESS_STATUSES = {"running", "working"}


def server_actions(server: dict) -> dict[str, bool]:
    """Return enabled states for server-level controls."""
    running = bool(server.get("running"))
    managed = bool(server.get("managed_by_launcher"))
    return {
        "can_start": not running,
        "can_stop": running and managed,
        "can_restart": running and managed,
    }


def process_actions(process: dict) -> dict[str, bool]:
    """Return enabled states for one process card."""
    status = process.get("status")
    managed = bool(process.get("started_by_launcher"))
    active = status in RUNNING_PROCESS_STATUSES
    stopped = status == "stopped"
    has_base = bool(process.get("base"))
    return {
        "can_start": stopped and has_base,
        "can_stop": managed and active,
        "can_restart": managed and active,
        "can_view_logs": managed,
        "is_external": not managed and status != "stopped",
    }
