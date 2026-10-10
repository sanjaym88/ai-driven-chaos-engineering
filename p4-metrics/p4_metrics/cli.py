import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import urljoin
from .traffic_generator import TrafficGenerator
from .recorder import MetricRecorder
from .windows import separate_windows
from .validation import load_metrics_file, validate_run_id_consistency
from .evaluation import evaluate_run

def resolve_request_target(service_map, plan):
    """Resolve the logical P3 target to the real user-facing route through its dependency proxy."""
    services = service_map.get("services", {})
    target_id = plan.get("target")

    # P1 verified service-map contract: services is a list, dependencies are explicit edges.
    if isinstance(services, list):
        service_by_id = {service.get("id"): service for service in services}
        if target_id not in service_by_id:
            raise ValueError(f"Target service '{target_id}' is missing from the P1 service map")
        edges = [edge for edge in service_map.get("dependencies", []) if edge.get("to") == target_id]
        route_override = plan.get("request_path")
        if route_override:
            edges = [edge for edge in edges if edge.get("path") == route_override]
        if not edges:
            raise ValueError(f"No mapped caller route reaches target '{target_id}'")
        if len(edges) > 1:
            raise ValueError(f"Target '{target_id}' has multiple caller routes; choose one with request_path in the plan")
        edge = edges[0]
        caller = service_by_id.get(edge.get("from"))
        if not caller or not caller.get("host_url"):
            raise ValueError(f"Mapped caller service '{edge.get('from')}' has no host_url")
        route = plan.get("request_path") or edge.get("path")
        if not isinstance(route, str) or " " not in route.strip():
            raise ValueError("Mapped request path must include an HTTP method and path, e.g. 'POST /echo'")
        method, path = route.strip().split(None, 1)
        request_url = plan.get("request_url") or urljoin(caller["host_url"].rstrip("/") + "/", path.lstrip("/"))
        health_path = caller.get("health_path", "")
        health_url = plan.get("health_url") or urljoin(caller["host_url"].rstrip("/") + "/", health_path.lstrip("/"))
        return request_url, health_url, method.upper()

    # Backward-compatible simple fixture shape.
    service_data = services.get(target_id, {}) if isinstance(services, dict) else {}
    target_url = plan.get("request_url") or service_data.get("url")
    if not target_url:
        raise ValueError(f"Service '{target_id}' has no request URL in the service map or plan")
    health_path = service_data.get("health_check_path", "")
    health_url = plan.get("health_url") or urljoin(target_url.rstrip("/") + "/", health_path.lstrip("/"))
    return target_url, health_url, plan.get("request_method", "GET").upper()

def generate_traffic(args):
    with open(args.service_map, 'r') as f:
        service_map = json.load(f)
    with open(args.experiment_plan, 'r') as f:
        plan = json.load(f)
        
    run_id = plan.get("run_id")
    target_service = plan.get("target")
    stop_thresholds = plan.get("stop_thresholds", {})
    
    if args.phase == "fault":
        duration = plan.get("max_duration", 30)
    else:
        duration = args.duration
        
    target_url, health_url, request_method = resolve_request_target(service_map, plan)
    request_body = plan.get("request_body")
    if request_method in {"POST", "PUT", "PATCH"} and request_body is None:
        request_body = {"p4_probe": "metrics traffic through mapped dependency"}
        
    recorder = MetricRecorder(args.output)
    generator = TrafficGenerator(
        target_url=target_url, 
        health_url=health_url,
        max_concurrency=args.concurrency,
        stop_thresholds=stop_thresholds,
        request_method=request_method,
        request_body=request_body,
        safety_flag_dir=str(Path(args.output).resolve().parent),
        p3_run_state_path=args.p3_run_state,
    )
    
    print(f"Integration Check: {request_method} {target_url} (Health: GET {health_url}) [Plan: {run_id}]")
    print(f"Safety Limits Enabled: {stop_thresholds}")
    print(f"Generating '{args.phase}' traffic for {duration}s...")
    
    asyncio.run(generator.run(
        run_id=run_id,
        phase=args.phase,
        duration_sec=duration,
        record_callback=recorder.record
    ))
    print(f"Traffic generation complete. Data appended to {args.output}")

def evaluate_metrics(args):
    with open(args.experiment_plan, 'r') as f:
        plan = json.load(f)
    run_id = plan.get("run_id")

    with open(args.p3_result, 'r', encoding='utf-8') as f:
        p3_result = json.load(f)

    print(f"Loading metrics from {args.metrics_file} for run '{run_id}'...")
    records = load_metrics_file(args.metrics_file)
    # The shared append-only feed contains multiple runs. Select this run
    # before applying the strict single-run contract; never pool their metrics.
    valid_records = validate_run_id_consistency([r for r in records if r.run_id == run_id], run_id)
    
    windows = separate_windows(valid_records)
    
    result = evaluate_run(
        run_id=run_id,
        baseline=windows["baseline"],
        fault=windows["fault"],
        recovery=windows["recovery"],
        p3_result=p3_result
    )
    
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, 'w', encoding='utf-8') as f:
        f.write(result.model_dump_json(indent=2))
        
    print(f"Evaluation complete. Result written to {args.output}")
    print(f"Status: {result.status}, Recovery: {result.recovery_result}")
    print(f"Detected Failure Flags (Feeds P2/P5): {result.failure_flags}")

def main():
    parser = argparse.ArgumentParser(description="P4 Metrics & Evaluation CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    traffic_parser = subparsers.add_parser("traffic", help="Generate bounded HTTP measurement traffic")
    traffic_parser.add_argument("--service-map", default="examples/service-map.example.json", help="P1 Service Map")
    traffic_parser.add_argument("--experiment-plan", default="contracts/experiment-plan.example.json", help="P3 Experiment Plan")
    traffic_parser.add_argument("--phase", required=True, choices=["baseline", "fault", "recovery"])
    traffic_parser.add_argument("--duration", type=int, default=10, help="Duration for non-fault phases")
    traffic_parser.add_argument("--concurrency", type=int, default=10, help="Max requests concurrency")
    traffic_parser.add_argument("--output", default="metrics.jsonl", help="Output JSONL file")
    traffic_parser.add_argument("--p3-run-state", help="For fault phase, stop when this P3 run.json reaches stopped/cleanup_failed")
    
    eval_parser = subparsers.add_parser("evaluate", help="Evaluate run metrics for reporting")
    eval_parser.add_argument("--experiment-plan", default="contracts/experiment-plan.example.json", help="P3 Experiment Plan")
    eval_parser.add_argument("--metrics-file", required=True, help="Path to metrics.jsonl")
    eval_parser.add_argument("--p3-result", required=True, help="Required path to P3's result.json file")
    eval_parser.add_argument("--output", default="result.json", help="Output result JSON file")
    
    args = parser.parse_args()
    
    if args.command == "traffic":
        generate_traffic(args)
    elif args.command == "evaluate":
        evaluate_metrics(args)

if __name__ == "__main__":
    main()
