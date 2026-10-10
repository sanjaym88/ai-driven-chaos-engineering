
"""P2 Experiment Planner: rank candidates and emit an execution plan."""

import json
import argparse
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path

from planner.ranker import rank_candidates
from planner.schemas import validate_inputs
from planner.data_source import collect_baseline

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


def build_experiment_plan(candidate, baseline, service_map=None):
    """Convert the top-ranked candidate to the shared P2/P3 contract."""

    original_fault = candidate["fault_type"]
    canonical_fault = FAULT_NAME_MAP.get(original_fault)

    if canonical_fault is None:
        raise ValueError(
            f"Cannot map fault type to P1 naming: {original_fault}"
        )

    intensity = candidate["intensity"]

    # P3's connection-disabled toxic is a numeric flag (1). P2 templates may
    # express that same lowest-risk setting with the human-readable "limited".
    if canonical_fault == "connection-disabled" and intensity == "limited":
        intensity = 1

    # Latency requires a numeric delay in milliseconds.
    if (
        not isinstance(intensity, (int, float))
        or isinstance(intensity, bool)
        or not isfinite(intensity)
        or intensity < 0
    ):
        raise ValueError(
            f"Intensity for {canonical_fault} must be a non-negative number."
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

    run_id = datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%S%fZ")

    plan = {
        "run_id": run_id,
        "candidate_id": candidate["candidate_id"],
        "target": candidate["target"],
        "fault_type": canonical_fault,
        "intensity": intensity,
        "max_duration": candidate["max_duration"],
        "stop_thresholds": stop_thresholds,
        "reason_codes": candidate["reason_codes"],
    }
    if service_map is not None:
        edges = [
            edge for edge in service_map.get("dependencies", [])
            if edge.get("to") == candidate["target"]
            and edge.get("verification") == "passed"
        ]
        if len(edges) == 1:
            plan["request_path"] = edges[0]["path"]
        elif len(edges) > 1:
            raise ValueError(
                f"Target {candidate['target']} has multiple caller routes; select one in the candidate."
            )
    return plan


def main():
    parser = argparse.ArgumentParser(description="P2 deterministic experiment planner")
    parser.add_argument(
        "--service-map",
        default=str(ROOT.parent / "chaos-execution" / "sandbox" / "service-map.json"),
        help="Verified P1/P3 service map JSON",
    )
    parser.add_argument("--baseline", help="Use this baseline JSON; omit to collect 30 live requests")
    parser.add_argument(
        "--fault-templates",
        default=str(FIXTURES / "fault-templates.json"),
        help="Approved fault-template JSON",
    )
    args = parser.parse_args()

    # A failed invocation must never leave an older executable plan behind.
    (ROOT / "experiment-plan.json").unlink(missing_ok=True)

    service_map = json.loads(Path(args.service_map).read_text(encoding="utf-8"))
    fault_templates = json.loads(Path(args.fault_templates).read_text(encoding="utf-8"))
    baseline = (
        json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        if args.baseline
        else collect_baseline(service_map)
    )

    observed_at = baseline.get("observed_at") or baseline.get("captured_at")
    if not observed_at:
        print("\nPlanning blocked: baseline has no observed_at/captured_at timestamp.")
        raise SystemExit(1)
    try:
        observed = datetime.fromisoformat(str(observed_at).replace("Z", "+00:00"))
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        baseline_age_seconds = (datetime.now(timezone.utc) - observed).total_seconds()
    except ValueError:
        print("\nPlanning blocked: baseline timestamp is invalid.")
        raise SystemExit(1)

    print("\n=== P2 Experiment Planner ===")
    print("Service map status:", service_map.get("verification_status"))
    print("Baseline health:", baseline.get("health"))
    print("Fault templates:", len(fault_templates.get("templates", [])))

    try:
        validate_inputs(service_map, baseline, fault_templates)
        ranked, rejected = rank_candidates(
            service_map,
            baseline,
            fault_templates,
            baseline_age_seconds=baseline_age_seconds,
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
            raise SystemExit(1)

        print("\nRanked candidates:")
        for position, candidate in enumerate(ranked, start=1):
            print(
                f"{position}. {candidate['candidate_id']} | "
                f"{candidate['fault_type']} | "
                f"score={candidate['score']}"
            )

        # Select exactly one candidate for this execution plan.
        plan = build_experiment_plan(ranked[0], baseline, service_map)
        plan_path = save_json("experiment-plan.json", plan)

        print("\n=== Proposed experiment plan ===")
        print(json.dumps(plan, indent=2))
        print(f"\nRanking saved to: {ranked_path}")
        print(f"Experiment plan saved to: {plan_path}")
        print("\nNOTE: Stop thresholds are provisional.")
        print("P3 must validate the plan and safety limits before execution.")

    except (ValueError, TypeError, KeyError, RuntimeError, OSError) as exc:
        print(f"\nPlanning blocked: {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
