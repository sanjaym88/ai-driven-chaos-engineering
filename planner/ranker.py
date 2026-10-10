"""Deterministic candidate ranking for P2: Experiment Planner."""

FAULT_PRIORITY = {
    "latency": 30,
    "timeout": 20,
    "connection-disabled": 10,
    "network_delay": 30,          # Compatibility with earlier fixtures
    "connection_failure": 10,     # Compatibility with earlier fixtures
}

DEFAULT_DURATION_SECONDS = 5


def _mapped_targets(service_map):
    """Get verified dependency targets from the application map."""
    service_ids = {
        service["id"]
        for service in service_map.get("services", [])
        if isinstance(service, dict) and service.get("id")
    }

    return {
        edge["to"]
        for edge in service_map.get("dependencies", [])
        if isinstance(edge, dict)
        and edge.get("to") in service_ids
        and edge.get("verification") == "passed"
    }


def _validate_baseline(
    baseline,
    baseline_age_seconds=None,
    max_baseline_age_seconds=300,
):
    """Block planning when baseline data is incomplete or unhealthy."""
    if not isinstance(baseline, dict):
        raise ValueError("Baseline must be a JSON object.")

    required = (
        "sample_count",
        "latency_p95_ms",
        "error_rate",
        "health",
    )

    missing = [key for key in required if key not in baseline]
    if missing:
        raise ValueError(f"Baseline missing fields: {missing}")

    if (
        not isinstance(baseline["sample_count"], int)
        or isinstance(baseline["sample_count"], bool)
        or baseline["sample_count"] <= 0
    ):
        raise ValueError("Baseline sample_count must be a positive integer.")

    latency = baseline["latency_p95_ms"]
    if (
        not isinstance(latency, (int, float))
        or isinstance(latency, bool)
        or latency < 0
    ):
        raise ValueError("Baseline latency must be non-negative.")

    error_rate = baseline["error_rate"]
    if (
        not isinstance(error_rate, (int, float))
        or isinstance(error_rate, bool)
        or not 0 <= error_rate <= 1
    ):
        raise ValueError("Baseline error_rate must be between 0 and 1.")

    if baseline["health"] != "healthy":
        raise ValueError("Baseline is not healthy; planning blocked.")

    if baseline_age_seconds is not None:
        if baseline_age_seconds < 0:
            raise ValueError("Baseline age cannot be negative.")

        if baseline_age_seconds > max_baseline_age_seconds:
            raise ValueError("Baseline is stale; planning blocked.")


def _choose_intensity(template):
    """Select the lowest allowed numeric intensity."""
    values = template.get("allowed_intensities_ms")

    if values is not None:
        if not isinstance(values, list) or not values:
            return None

        if any(
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or value < 0
            for value in values
        ):
            return None

        return min(values)

    named_values = template.get("allowed_intensities", [])

    if isinstance(named_values, list) and named_values:
        return named_values[0]

    return None


def _build_candidate(template, mapped_targets):
    """Validate a candidate and return it with a deterministic score."""
    if not isinstance(template, dict):
        return None, "INVALID_TEMPLATE"

    candidate_id = template.get("candidate_id")
    target = template.get("target")
    fault_type = template.get("fault_type")
    max_duration = template.get("max_duration_seconds")

    if not candidate_id or not isinstance(candidate_id, str):
        return None, "INVALID_CANDIDATE_ID"

    if target not in mapped_targets:
        return None, "TARGET_NOT_MAPPED"

    if fault_type not in FAULT_PRIORITY:
        return None, "UNSUPPORTED_FAULT_TYPE"

    if (
        not isinstance(max_duration, (int, float))
        or isinstance(max_duration, bool)
        or max_duration <= 0
    ):
        return None, "INVALID_DURATION"

    intensity = _choose_intensity(template)
    if intensity is None:
        return None, "INVALID_INTENSITY"

    duration = min(DEFAULT_DURATION_SECONDS, max_duration)

    # Starter heuristic, not a probability of failure.
    score = FAULT_PRIORITY[fault_type] - duration

    candidate = {
        "candidate_id": candidate_id,
        "target": target,
        "fault_type": fault_type,
        "intensity": intensity,
        "max_duration": duration,
        "score": score,
        "reason_codes": [
            "APPROVED_TEMPLATE",
            "MAPPED_DEPENDENCY",
            "BASELINE_HEALTHY",
        ],
    }

    return candidate, None


def rank_candidates(
    service_map,
    baseline,
    fault_templates,
    baseline_age_seconds=None,
    max_baseline_age_seconds=300,
):
    """
    Rank approved candidates deterministically.

    Returns:
        (ranked_candidates, rejected_candidates)
    """
    _validate_baseline(
        baseline,
        baseline_age_seconds,
        max_baseline_age_seconds,
    )

    if not isinstance(service_map, dict):
        raise ValueError("Service map must be a JSON object.")

    if service_map.get("verification_status") != "verified":
        raise ValueError("Application service map is not verified.")

    if not isinstance(fault_templates, dict):
        raise ValueError("Fault templates must be a JSON object.")

    services = service_map.get("services", [])
    dependencies = service_map.get("dependencies", [])

    if not isinstance(services, list) or not isinstance(dependencies, list):
        raise ValueError("Invalid services or dependencies structure.")

    mapped_targets = _mapped_targets(service_map)

    templates = fault_templates.get("templates", [])
    if not isinstance(templates, list):
        raise ValueError("'templates' must be a list.")

    ranked = []
    rejected = []

    for template in templates:
        candidate, reason = _build_candidate(template, mapped_targets)

        if candidate is None:
            rejected.append({
                "candidate_id": (
                    template.get("candidate_id", "unknown")
                    if isinstance(template, dict)
                    else "unknown"
                ),
                "reason": reason,
            })
        else:
            ranked.append(candidate)

    # Highest score first; candidate ID breaks ties consistently.
    ranked.sort(
        key=lambda item: (-item["score"], item["candidate_id"])
    )

    return ranked, rejected