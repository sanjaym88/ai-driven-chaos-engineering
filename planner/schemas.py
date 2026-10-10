"""Structural validation for P2 planner inputs.

The planner intentionally uses plain dictionaries so its JSON contracts are easy
to share with P1, P3, and P4. Invalid top-level inputs raise ``ValueError``;
individual candidate problems are handled by the ranker as rejected candidates.
"""

from math import isfinite


def _object(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object.")


def _non_empty_string(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string.")


def _number(value, label, *, minimum=None, maximum=None):
    try:
        finite = isfinite(value)
    except (OverflowError, TypeError):
        finite = False
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not finite:
        raise ValueError(f"{label} must be a finite number.")
    if minimum is not None and value < minimum:
        qualifier = "non-negative" if minimum == 0 else f"at least {minimum}"
        raise ValueError(f"{label} must be {qualifier}.")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label} must be at most {maximum}.")


def validate_inputs(service_map, baseline, fault_templates):
    """Validate P1 topology, P4 baseline, and fault-template container shapes.

    Returns ``True`` when the structures are valid. Freshness and health policy
    are checked by the ranker because they depend on run-time context.
    """
    _object(service_map, "Service map")
    if service_map.get("verification_status") != "verified":
        raise ValueError("Application service map is not verified.")
    services = service_map.get("services")
    dependencies = service_map.get("dependencies")
    if not isinstance(services, list) or not services:
        raise ValueError("Service map 'services' must be a non-empty list.")
    if not isinstance(dependencies, list):
        raise ValueError("Service map 'dependencies' must be a list.")

    service_ids = set()
    for index, service in enumerate(services):
        _object(service, f"services[{index}]")
        service_id = service.get("id")
        _non_empty_string(service_id, f"services[{index}].id")
        if service_id in service_ids:
            raise ValueError(f"Duplicate service id: {service_id}")
        service_ids.add(service_id)

    for index, edge in enumerate(dependencies):
        _object(edge, f"dependencies[{index}]")
        for field in ("from", "to"):
            if edge.get(field) not in service_ids:
                raise ValueError(f"dependencies[{index}].{field} references an unknown service.")
        caller = next(item for item in services if item.get("id") == edge["from"])
        _non_empty_string(caller.get("host_url"), f"dependency caller {edge['from']}.host_url")
        _non_empty_string(edge.get("path"), f"dependencies[{index}].path")
        if len(edge["path"].split()) < 2:
            raise ValueError(f"dependencies[{index}].path must include an HTTP method and path.")

    _object(baseline, "Baseline")
    required_baseline = ("sample_count", "latency_p95_ms", "error_rate", "health")
    missing = [key for key in required_baseline if key not in baseline]
    if missing:
        raise ValueError(f"Baseline missing fields: {missing}")
    count = baseline["sample_count"]
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise ValueError("Baseline sample_count must be a positive integer.")
    _number(baseline["latency_p95_ms"], "Baseline latency_p95_ms", minimum=0)
    _number(baseline["error_rate"], "Baseline error_rate", minimum=0, maximum=1)
    if not isinstance(baseline["health"], str) or baseline["health"] not in {"healthy", "unhealthy", "degraded", "unknown"}:
        raise ValueError("Baseline health must be healthy, unhealthy, degraded, or unknown.")

    _object(fault_templates, "Fault templates")
    templates = fault_templates.get("templates")
    if not isinstance(templates, list):
        raise ValueError("Fault templates 'templates' must be a list.")
    return True
