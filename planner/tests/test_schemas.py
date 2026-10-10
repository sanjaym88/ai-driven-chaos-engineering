import copy
import json
from pathlib import Path

import pytest

from planner.schemas import validate_inputs


FIXTURES = Path(__file__).parents[1] / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_p1_p4_fixtures_pass_p2_input_validation():
    assert validate_inputs(
        load("service-map.json"),
        load("baseline.json"),
        load("fault-templates.json"),
    ) is True


@pytest.mark.parametrize("field,value,match", [
    ("verification_status", "unverified", "not verified"),
    ("services", [], "non-empty list"),
    ("dependencies", {}, "must be a list"),
])
def test_bad_service_map_is_rejected(field, value, match):
    service_map = load("service-map.json")
    service_map[field] = value
    with pytest.raises(ValueError, match=match):
        validate_inputs(service_map, load("baseline.json"), load("fault-templates.json"))


@pytest.mark.parametrize("field,value,match", [
    ("sample_count", True, "positive integer"),
    ("latency_p95_ms", -1, "non-negative"),
    ("error_rate", 1.1, "at most 1"),
    ("health", "unknown-ish", "health must be"),
])
def test_bad_baseline_is_rejected(field, value, match):
    baseline = load("baseline.json")
    baseline[field] = value
    with pytest.raises(ValueError, match=match):
        validate_inputs(load("service-map.json"), baseline, load("fault-templates.json"))


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_non_finite_baseline_numbers_are_rejected(value):
    baseline = load("baseline.json")
    baseline["latency_p95_ms"] = value
    with pytest.raises(ValueError, match="finite number"):
        validate_inputs(load("service-map.json"), baseline, load("fault-templates.json"))


def test_bad_dependency_route_is_rejected():
    service_map = load("service-map.json")
    service_map["dependencies"][0]["path"] = "/echo"
    with pytest.raises(ValueError, match="HTTP method"):
        validate_inputs(service_map, load("baseline.json"), load("fault-templates.json"))


def test_fault_template_container_must_be_a_list():
    templates = copy.deepcopy(load("fault-templates.json"))
    templates["templates"] = {}
    with pytest.raises(ValueError, match="must be a list"):
        validate_inputs(load("service-map.json"), load("baseline.json"), templates)
