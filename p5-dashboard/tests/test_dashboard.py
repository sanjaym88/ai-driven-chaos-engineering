"""Renders every dashboard page headlessly (no browser needed) and fails on any error.
Catches problems like a Streamlit version not supporting an option."""
from pathlib import Path

import pytest

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
APP = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.mark.parametrize("run_id", ["run-0001", "run-0002", "run-0003", "run-0004"])
def test_run_detail_renders(run_id, tmp_path, monkeypatch):
    monkeypatch.setenv("P5_OUT_DIR", str(tmp_path))
    at = AppTest.from_file(APP, default_timeout=60)
    at.query_params["run"] = run_id
    at.run()
    assert not at.exception, at.exception[0].message
    assert [m.value for m in at.metric][:1] == ["4"]          # 4 sample runs counted


@pytest.mark.parametrize("page", ["Service map", "Notifications"])
def test_other_pages_render(page, tmp_path, monkeypatch):
    monkeypatch.setenv("P5_OUT_DIR", str(tmp_path))
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.sidebar.radio[0].set_value(page).run()
    assert not at.exception, at.exception[0].message
