import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("dashboard_server", Path(__file__).parents[1] / "server.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_only_selected_run_contributes_and_missing_is_unknown(tmp_path):
    runs = tmp_path / "runs"
    (runs / "one").mkdir(parents=True)
    (runs / "one" / "result.json").write_text(json.dumps({"run_id":"one","status":"completed"}))
    metrics = tmp_path / "metrics.jsonl"
    metrics.write_text('\n'.join(json.dumps({"run_id":rid,"phase":"baseline","request_count":1,"errors":0,"latency":{"values":[value]},"timestamp":"2026-10-10"}) for rid,value in [("one",10),("two",999)]))
    data = module.Artifacts(runs, metrics, tmp_path / "map.json").detail("one")
    assert data["summary"]["baseline"]["p95"] == 10
    assert data["summary"]["recovery"]["p95"] is None
    assert data["p4"] is None


def test_paths_cannot_escape_artifact_directory(tmp_path):
    source = module.Artifacts(tmp_path, tmp_path / "metrics", tmp_path / "map")
    for value in ("../secret", "..", "a/b", "a\\b", "C:secret"):
        with pytest.raises(ValueError): source.directory(value)


def test_partial_jsonl_line_is_reported_and_mismatched_result_excluded(tmp_path):
    (tmp_path / "one").mkdir()
    (tmp_path / "one/result.json").write_text('{"run_id":"other","cleanup_verified":true}')
    (tmp_path / "metrics").write_text('{"run_id":')
    data = module.Artifacts(tmp_path, tmp_path / "metrics", tmp_path / "map").detail("one")
    assert data["p3"] is None
    assert len(data["warnings"]) == 2


def test_empty_directory_lists_no_runs(tmp_path):
    assert module.Artifacts(tmp_path, tmp_path / "metrics", tmp_path / "map").listing()["runs"] == []
