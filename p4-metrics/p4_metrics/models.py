from datetime import datetime
from typing import List, Literal, Optional, Dict
from pydantic import BaseModel, Field

class LatencySummary(BaseModel):
    unit: Literal["ms"] = "ms"
    values: List[float] = Field(default_factory=list)
    
    # ADDON FOR P5: Percentiles computed automatically so the dashboard UI doesn't have to do the math.
    p50: Optional[float] = None
    p95: Optional[float] = None
    p99: Optional[float] = None

class MetricRecord(BaseModel):
    run_id: str
    timestamp: datetime
    phase: Literal["baseline", "fault", "recovery"]
    request_count: int
    errors: int
    latency: LatencySummary
    health: Literal["healthy", "degraded", "unhealthy", "unknown"]
    
    status_code: Optional[int] = None
    throughput_rps: Optional[float] = None
    window_start: Optional[datetime] = None
    window_end: Optional[datetime] = None
    source: Optional[str] = None
    sample_count: Optional[int] = None
    schema_version: Optional[str] = "1.0"

class ExperimentPlan(BaseModel):
    run_id: str
    candidate_id: str
    target: str
    fault_type: str
    intensity: float
    max_duration: int
    stop_thresholds: Dict[str, float]
    reason_codes: List[str]

class ResultEvaluation(BaseModel):
    run_id: str
    status: Literal["valid", "invalid"]
    stop_reason: Optional[str] = None
    cleanup_verified: bool
    recovery_result: Literal["success", "incomplete", "failed", "unknown"]
    failure_flags: List[str] = Field(default_factory=list) 
    evidence_refs: List[str] = Field(default_factory=list)

class ServiceEndpoint(BaseModel):
    service_name: str
    url: str
    health_check_path: str
