from datetime import datetime
from typing import Annotated, List, Literal, Optional, Dict
from pydantic import BaseModel, Field, model_validator

class LatencySummary(BaseModel):
    unit: Literal["ms"] = "ms"
    values: List[Annotated[float, Field(ge=0)]] = Field(default_factory=list)
    
    # ADDON FOR P5: Percentiles computed automatically so the dashboard UI doesn't have to do the math.
    p50: Optional[float] = Field(default=None, ge=0)
    p95: Optional[float] = Field(default=None, ge=0)
    p99: Optional[float] = Field(default=None, ge=0)

class MetricRecord(BaseModel):
    run_id: str = Field(min_length=1)
    timestamp: datetime
    phase: Literal["baseline", "fault", "recovery"]
    request_count: int = Field(ge=0)
    errors: int = Field(ge=0)
    latency: LatencySummary
    health: Literal["healthy", "degraded", "unhealthy", "unknown"]
    status_codes: Dict[str, int] = Field(default_factory=dict)
    request_method: Optional[str] = None
    request_url: Optional[str] = None
    
    status_code: Optional[int] = None
    throughput_rps: Optional[float] = None
    window_start: Optional[datetime] = None
    window_end: Optional[datetime] = None
    source: Optional[str] = None
    sample_count: Optional[int] = None
    schema_version: Optional[str] = "1.0"

    @model_validator(mode="after")
    def validate_counts(self):
        if self.errors > self.request_count:
            raise ValueError("errors cannot exceed request_count")
        if sum(self.status_codes.values()) > self.request_count:
            raise ValueError("status_codes count cannot exceed request_count")
        return self

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
    p3_status: Optional[Literal["completed", "aborted", "failed", "blocked"]] = None
    stop_reason: Optional[str] = None
    cleanup_verified: bool
    recovery_result: Literal["success", "incomplete", "failed", "unknown"]
    failure_flags: List[str] = Field(default_factory=list) 
    evidence_refs: List[str] = Field(default_factory=list)

class ServiceEndpoint(BaseModel):
    service_name: str
    url: str
    health_check_path: str
