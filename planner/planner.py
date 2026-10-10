
"""P2 Experiment Planner: rank candidates and emit an execution plan."""

import json
from datetime import datetime, timezone
from pathlib import Path

from planner.ranker import rank_candidates

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"

# Compatibility mappings: confirm canonical names with P3.
FAULT_NAME_MAP = {
    "network_delay": "latency",
    "latency": "latency",
    "connection_failure": "connection-disabled",
    "connection-disabled": "connection-disabled",
    "timeout": "timeout",
}


def load_json(filename):
    """Load a JSON fixture."""
    path = FIXTURES / filename
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_json(filename, data):
    """Write a JSON artifact."""
    path = ROOT / filename
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)
        file.write("\n")
    return path


def build_experiment_plan(candidate, baseline):
    """Convert the top-ranked candidate to the shared P2/P3 contract."""

    original_fault = candidate["fault_type"]
    canonical_fault = FAULT_NAME_MAP.get(original_fault)

    if canonical_fault is None:
        raise ValueError(
            f"Cannot map fault type to P1 naming: {original_fault}"
        )

    intensity = candidate["intensity"]

    # Latency requires a numeric delay in milliseconds.
    if canonical_fault == "latency":
        if (
            not isinstance(intensity, (int, float))
            or isinstance(intensity, bool)
            or intensity < 0
        ):
            raise ValueError(
                "Latency intensity must be a non-negative number in ms."
            )

    # Provisional safety thresholds for team review.
    # P3 must confirm these before executing experiments.
    baseline_p95 = baseline["latency_p95_ms"]
    baseline_error_rate = baseline["error_rate"]

    stop_thresholds = {
        "max_error_rate": min(1.0, max(0.05, baseline_error_rate + 0.05)),
        "max_latency_p95_ms": max(500, baseline_p95 * 2),
        "required_health": "healthy",
    }

    run_id = datetime.now(timezone.utc).strftime(
        "run-%Y%m%dT%H%M%SZ"
    )

    return {
        "run_id": run_id,
        "candidate_id": candidate["candidate_id"],
        "target": candidate["target"],
        "fault_type": canonical_fault,
        "intensity": intensity,
        "max_duration": candidate["max_duration"],
        "stop_thresholds": stop_thresholds,
        "reason_codes": candidate["reason_codes"],
    }


def main():
    service_map = load_json("service-map.json")
    baseline = load_json("baseline.json")
    fault_templates = load_json("fault-templates.json")

    print("\n=== P2 Experiment Planner ===")
    print("Service map status:", service_map.get("verification_status"))
    print("Baseline health:", baseline.get("health"))
    print("Fault templates:", len(fault_templates.get("templates", [])))

    try:
        ranked, rejected = rank_candidates(
            service_map,
            baseline,
            fault_templates,
        )

        # Preserve the full ranking for debugging and evaluation.
        ranked_output = {
            "ranked_candidates": ranked,
            "rejected_candidates": rejected,
        }
        ranked_path = save_json(
            "ranked-candidates.json",
            ranked_output,
        )

        if rejected:
            print("\nRejected candidates:")
            for item in rejected:
                print(f"  - {item['candidate_id']}: {item['reason']}")

        if not ranked:
            print("\nNo valid experiment candidates found.")
            print("No experiment plan generated.")
            return

        print("\nRanked candidates:")
        for position, candidate in enumerate(ranked, start=1):
            print(
                f"{position}. {candidate['candidate_id']} | "
                f"{candidate['fault_type']} | "
                f"score={candidate['score']}"
            )

        # Select exactly one candidate for this execution plan.
        plan = build_experiment_plan(ranked[0], baseline)
        plan_path = save_json("experiment-plan.json", plan)

        print("\n=== Proposed experiment plan ===")
        print(json.dumps(plan, indent=2))
        print(f"\nRanking saved to: {ranked_path}")
        print(f"Experiment plan saved to: {plan_path}")
        print("\nNOTE: Stop thresholds are provisional.")
        print("P3 must validate the plan and safety limits before execution.")

    except (ValueError, TypeError, KeyError) as exc:
        print(f"\nPlanning blocked: {exc}")


if __name__ == "__main__":
    main()
