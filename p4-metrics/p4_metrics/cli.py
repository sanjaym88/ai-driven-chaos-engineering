import argparse
import asyncio
import json
from .traffic_generator import TrafficGenerator
from .recorder import MetricRecorder
from .windows import separate_windows
from .validation import load_metrics_file, validate_run_id_consistency
from .evaluation import evaluate_run

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
        
    service_data = service_map.get("services", {}).get(target_service, {})
    target_url = service_data.get("url")
    health_path = service_data.get("health_check_path", "")
    
    if not target_url:
        raise ValueError(f"Integration Error: Service '{target_service}' not found in {args.service_map}")
        
    # Construct distinct health url for P1 addon
    health_url = f"{target_url.rstrip('/')}{health_path}" if health_path else target_url
        
    recorder = MetricRecorder(args.output)
    generator = TrafficGenerator(
        target_url=target_url, 
        health_url=health_url,
        max_concurrency=args.concurrency,
        stop_thresholds=stop_thresholds
    )
    
    print(f"Integration Check: Hitting {target_url} (Health: {health_url}) [Plan: {run_id}]")
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

    print(f"Loading metrics from {args.metrics_file} for run '{run_id}'...")
    records = load_metrics_file(args.metrics_file)
    valid_records = validate_run_id_consistency(records, run_id)
    
    windows = separate_windows(valid_records)
    
    result = evaluate_run(
        run_id=run_id,
        baseline=windows["baseline"],
        fault=windows["fault"],
        recovery=windows["recovery"]
    )
    
    with open(args.output, 'w') as f:
        f.write(result.model_dump_json(indent=2))
        
    print(f"Evaluation complete. Result written to {args.output}")
    print(f"Status: {result.status}, Recovery: {result.recovery_result}")
    print(f"Detected Failure Flags (Feeds P2/P5): {result.failure_flags}")

def main():
    parser = argparse.ArgumentParser(description="P4 Metrics & Evaluation CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    traffic_parser = subparsers.add_parser("traffic", help="Generate steady bounded traffic")
    traffic_parser.add_argument("--service-map", default="examples/service-map.example.json", help="P1 Service Map")
    traffic_parser.add_argument("--experiment-plan", default="contracts/experiment-plan.example.json", help="P3 Experiment Plan")
    traffic_parser.add_argument("--phase", required=True, choices=["baseline", "fault", "recovery"])
    traffic_parser.add_argument("--duration", type=int, default=10, help="Duration for non-fault phases")
    traffic_parser.add_argument("--concurrency", type=int, default=10, help="Max requests concurrency")
    traffic_parser.add_argument("--output", default="metrics.jsonl", help="Output JSONL file")
    
    eval_parser = subparsers.add_parser("evaluate", help="Evaluate run metrics for reporting")
    eval_parser.add_argument("--experiment-plan", default="contracts/experiment-plan.example.json", help="P3 Experiment Plan")
    eval_parser.add_argument("--metrics-file", required=True, help="Path to metrics.jsonl")
    eval_parser.add_argument("--output", default="result.json", help="Output result JSON file")
    
    args = parser.parse_args()
    
    if args.command == "traffic":
        generate_traffic(args)
    elif args.command == "evaluate":
        evaluate_metrics(args)

if __name__ == "__main__":
    main()
