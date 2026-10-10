
import json
from pathlib import Path

from planner.ranker import rank_candidates

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


def load_fixture(name):
    with (FIXTURES / name).open(encoding="utf-8") as f:
        return json.load(f)


def test_candidates_are_ranked_highest_score_first():
    service_map = load_fixture("service-map.json")
    baseline = load_fixture("baseline.json")
    templates = load_fixture("fault-templates.json")

    ranked, rejected = rank_candidates(service_map, baseline, templates)

    assert ranked
    assert not rejected
    assert [c["score"] for c in ranked] == sorted(
        [c["score"] for c in ranked], reverse=True
    )


def test_unmapped_target_is_rejected():
    service_map = load_fixture("service-map.json")
    baseline = load_fixture("baseline.json")
    templates = load_fixture("fault-templates.json")

    templates["templates"][0]["target"] = "unknown-service"

    ranked, rejected = rank_candidates(service_map, baseline, templates)

    assert all(c["target"] != "unknown-service" for c in ranked)
    assert any(r["reason"] == "TARGET_NOT_MAPPED" for r in rejected)
