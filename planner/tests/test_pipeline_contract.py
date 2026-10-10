import json
import sys
from pathlib import Path

from planner.planner import build_experiment_plan
from planner.ranker import rank_candidates


ROOT = Path(__file__).resolve().parents[2]
P3_ROOT = ROOT / "chaos-execution"
sys.path.insert(0, str(P3_ROOT))

from chaos.config import load_safety, load_service_map  # noqa: E402
from chaos.validator import validate_plan  # noqa: E402


def load_fixture(name):
    path = Path(__file__).parents[1] / "fixtures" / name
    return json.loads(path.read_text(encoding="utf-8"))


def test_p2_generated_plan_is_accepted_by_p3_with_mapped_p4_route():
    service_map = json.loads((P3_ROOT / "sandbox" / "service-map.json").read_text(encoding="utf-8"))
    baseline = load_fixture("baseline.json")
    templates = load_fixture("fault-templates.json")
    ranked, _ = rank_candidates(service_map, baseline, templates)
    plan = build_experiment_plan(ranked[0], baseline, service_map)

    assert plan["request_path"] == "POST /echo"
    assert validate_plan(plan, load_safety(), load_service_map()).ok


def test_connection_disabled_named_intensity_becomes_p3_numeric_flag():
    candidate = {
        "candidate_id": "connection-failure-001",
        "target": "podinfo-backend",
        "fault_type": "connection_failure",
        "intensity": "limited",
        "max_duration": 5,
        "reason_codes": ["APPROVED_TEMPLATE"],
    }
    plan = build_experiment_plan(candidate, load_fixture("baseline.json"), load_fixture("service-map.json"))
    assert plan["fault_type"] == "connection-disabled"
    assert plan["intensity"] == 1
    assert validate_plan(plan, load_safety(), load_service_map()).ok


def test_blocked_planner_removes_old_plan_and_exits_nonzero(tmp_path, monkeypatch):
    import pytest
    import planner.planner as module
    monkeypatch.setattr(module, "ROOT", tmp_path)
    (tmp_path / "experiment-plan.json").write_text('{"run_id":"old"}')
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(load_fixture("baseline.json")))
    monkeypatch.setattr(sys, "argv", ["planner", "--service-map", str(P3_ROOT / "sandbox/service-map.json"),
        "--baseline", str(baseline_path), "--fault-templates", str(Path(__file__).parents[1] / "fixtures/fault-templates.json")])
    with pytest.raises(SystemExit) as exc:
        module.main()
    assert exc.value.code == 1
    assert not (tmp_path / "experiment-plan.json").exists()
