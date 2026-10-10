# P4 Metrics Evaluation

## Setup

```bash
cd p4-metrics
python -m venv .venv
# Activate the virtual environment:
# Windows: .\.venv\Scripts\activate
# Unix/MacOS: source .venv/bin/activate

pip install -e .
```

## Running the CLI

### 1. Generate Traffic
To generate steady traffic to the target service (configured in `examples/service-map.example.json`):

```bash
# Generate baseline traffic
p4-metrics traffic --phase baseline --duration 30

# Generate fault traffic
p4-metrics traffic --phase fault

# Generate recovery traffic
p4-metrics traffic --phase recovery --duration 30
```

This will produce a `metrics.jsonl` file with metric records for the different phases.

### 2. Evaluate Run
After traffic generation finishes and P3 evaluates the cleanup, evaluate the run metrics against the baseline:

```bash
p4-metrics evaluate --metrics-file metrics.jsonl --p3-result contracts/result.example.json
```

This will produce a `result.json` summarizing the run, any detected failure flags, and the recovery state.
