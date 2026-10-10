
import json
from pathlib import Path

import pytest

from planner.ranker import rank_candidates

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


def load_fixture(name):
    with (FIXTURES / name).open(encoding="utf-8") as f:
        return json.load(f)


def test_unhealthy_baseline_blocks_planning():
    service_map = load_fixture("service-map.json")
    baseline = load_fixture("baseline.json")
    templates = load_fixture("fault-templates.json")

    baseline["health"] = "unhealthy"

    with pytest.raises(ValueError, match="not healthy"):
        rank_candidates(service_map, baseline, templates)


def test_unverified_service_map_blocks_planning():
    service_map = load_fixture("service-map.json")
    baseline = load_fixture("baseline.json")
    templates = load_fixture("fault-templates.json")

    service_map["verification_status"] = "unverified"

    with pytest.raises(ValueError, match="not verified"):
        rank_candidates(service_map, baseline, templates)


def test_stale_baseline_blocks_planning():
    service_map = load_fixture("service-map.json")
    baseline = load_fixture("baseline.json")
    templates = load_fixture("fault-templates.json")

    with pytest.raises(ValueError, match="stale"):
        rank_candidates(
            service_map,
            baseline,
            templates,
            baseline_age_seconds=301,
            max_baseline_age_seconds=300,
        )


def test_invalid_error_rate_blocks_planning():
    service_map = load_fixture("service-map.json")
    baseline = load_fixture("baseline.json")
    templates = load_fixture("fault-templates.json")

    baseline["error_rate"] = 1.5

    with pytest.raises(ValueError, match="error_rate"):
        rank_candidates(service_map, baseline, templates)
