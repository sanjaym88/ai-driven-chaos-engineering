
import json
from pathlib import Path

from planner.ranker import rank_candidates

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


def load_fixture(name):
    with (FIXTURES / name).open(encoding="utf-8") as f:
        return json.load(f)


def test_identical_inputs_produce_identical_rankings():
    service_map = load_fixture("service-map.json")
    baseline = load_fixture("baseline.json")
    templates = load_fixture("fault-templates.json")

    first = rank_candidates(service_map, baseline, templates)
    second = rank_candidates(service_map, baseline, templates)

    assert first == second


def test_tied_scores_are_sorted_by_candidate_id():
    service_map = load_fixture("service-map.json")
    baseline = load_fixture("baseline.json")

    templates = {
        "templates": [
            {
                "candidate_id": "candidate-z",
                "target": "podinfo-backend",
                "fault_type": "latency",
                "max_duration_seconds": 5,
                "allowed_intensities_ms": [100],
            },
            {
                "candidate_id": "candidate-a",
                "target": "podinfo-backend",
                "fault_type": "latency",
                "max_duration_seconds": 5,
                "allowed_intensities_ms": [100],
            },
        ]
    }

    ranked, rejected = rank_candidates(service_map, baseline, templates)

    assert not rejected
    assert [c["candidate_id"] for c in ranked] == [
        "candidate-a",
        "candidate-z",
    ]
