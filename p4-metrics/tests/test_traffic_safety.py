import asyncio
import json
from pathlib import Path

from p4_metrics.cli import resolve_request_target
from p4_metrics.traffic_generator import TrafficGenerator


def test_both_safety_thresholds_are_checked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    generator = TrafficGenerator(
        "http://example.test/echo",
        stop_thresholds={"max_latency_p95_ms": 100, "max_error_rate": 0.2},
    )
    generator._check_safety_limits([10, 20, 30, 200], 0.5, "both")
    flag = (tmp_path / "safety-trip-both.flag").read_text()
    assert "LATENCY_P95_BREACH" in flag
    assert "ERROR_RATE_BREACH" in flag


def test_error_threshold_checked_when_latency_threshold_present(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    generator = TrafficGenerator(
        "http://example.test/echo",
        stop_thresholds={"max_latency_p95_ms": 1000, "max_error_rate": 0.1},
    )
    generator._check_safety_limits([10, 20, 30, 40], 0.5, "errors")
    assert "ERROR_RATE_BREACH" in (tmp_path / "safety-trip-errors.flag").read_text()


def test_safety_trip_is_written_beside_shared_metrics_file(tmp_path):
    output_dir = tmp_path / "chaos-execution" / "runs"
    generator = TrafficGenerator(
        "http://example.test/echo",
        stop_thresholds={"max_error_rate": 0.1},
        safety_flag_dir=str(output_dir),
    )
    generator._check_safety_limits([10], 0.5, "shared-path")
    assert (output_dir / "safety-trip-shared-path.flag").read_text().startswith("ERROR_RATE_BREACH")


def test_fault_sampler_detects_terminal_p3_run_state(tmp_path):
    state_path = tmp_path / "run.json"
    state_path.write_text(json.dumps({"state": "stopped"}), encoding="utf-8")
    generator = TrafficGenerator("http://example.test/echo", p3_run_state_path=str(state_path))
    assert generator._p3_run_finished()


def test_fault_phase_exits_immediately_after_p3_terminal_state(tmp_path):
    import time

    state_path = tmp_path / "run.json"
    state_path.write_text(json.dumps({"state": "cleanup_failed"}), encoding="utf-8")
    records = []
    generator = TrafficGenerator(
        "http://127.0.0.1:1/echo",
        p3_run_state_path=str(state_path),
    )

    started = time.monotonic()
    asyncio.run(generator.run("early-stop", "fault", 30, records.append))
    assert time.monotonic() - started < 1
    assert records == []


def test_p1_service_map_resolves_post_echo_through_frontend():
    service_map = json.loads((Path(__file__).parents[1] / "examples" / "service-map.example.json").read_text())
    plan = {"target": "podinfo-backend"}
    assert resolve_request_target(service_map, plan) == (
        "http://localhost:9898/echo",
        "http://localhost:9898/healthz",
        "POST",
    )


def test_make_request_sends_configured_post_json():
    from aiohttp import ClientSession, web

    async def scenario():
        received = {}

        async def handler(request):
            received["method"] = request.method
            received["body"] = await request.json()
            return web.json_response({"ok": True})

        app = web.Application()
        app.router.add_route("*", "/echo", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        generator = TrafficGenerator(f"http://127.0.0.1:{port}/echo", request_method="POST", request_body={"hello": "P4"})
        async with ClientSession() as session:
            latency, error, status = await generator._make_request(session, generator.target_url, generator.request_method, generator.request_body)
        await runner.cleanup()
        return received, latency, error, status

    received, latency, error, status = asyncio.run(scenario())
    assert received == {"method": "POST", "body": {"hello": "P4"}}
    assert latency >= 0
    assert error is False and status == 200


def test_run_records_time_windows_while_requests_arrive_at_fixed_rate():
    from aiohttp import web

    async def scenario():
        async def handler(request):
            if request.method == "POST":
                await request.read()
            return web.json_response({"ok": True})

        app = web.Application()
        app.router.add_route("*", "/echo", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        records = []
        generator = TrafficGenerator(
            f"http://127.0.0.1:{port}/echo",
            health_url=f"http://127.0.0.1:{port}/echo",
            request_method="POST",
            request_body={"sample": True},
        )
        await generator.run("rate-test", "baseline", 1, records.append)
        await runner.cleanup()
        return records

    records = asyncio.run(scenario())
    request_count = sum(record.request_count for record in records)
    assert 6 <= request_count <= 14
    assert records
    assert all(record.phase == "baseline" and record.request_method == "POST" for record in records)
    assert all(record.window_start is not None and record.window_end is not None for record in records)


def test_metric_record_json_has_http_observation_context():
    from datetime import datetime, timezone
    from p4_metrics.models import LatencySummary, MetricRecord

    record = MetricRecord(
        run_id="r1", timestamp=datetime.now(timezone.utc), phase="fault", request_count=10,
        errors=1, latency=LatencySummary(values=[1.0], p95=1.0), health="degraded",
        status_codes={"200": 9, "500": 1}, request_method="POST", request_url="http://localhost:9898/echo",
    )
    saved = json.loads(record.model_dump_json())
    assert saved["status_codes"] == {"200": 9, "500": 1}
    assert saved["request_method"] == "POST"
    assert saved["latency"]["p95"] == 1.0


def test_fault_sampler_waits_for_injection(tmp_path):
    async def scenario():
        path = tmp_path / "run.json"
        path.write_text(json.dumps({"state": "armed"}))
        gen = TrafficGenerator("http://unused.test", p3_run_state_path=str(path))
        calls, records = [], []
        async def request(*args):
            calls.append(gen._p3_state())
            return 1, False, 200
        gen._make_request = request
        task = asyncio.create_task(gen.run("waiting", "fault", 0.2, records.append))
        await asyncio.sleep(0.1)
        assert calls == []
        path.write_text(json.dumps({"state": "injected"}))
        await task
        assert calls and all(state == "injected" for state in calls)
    asyncio.run(scenario())


def test_zero_concurrency_rejected_instead_of_hanging():
    import pytest
    with pytest.raises(ValueError):
        TrafficGenerator("http://unused.test", max_concurrency=0)
