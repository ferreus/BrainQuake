from datetime import datetime
from typing import Optional, Dict, Any
from pydantic import BaseModel, ConfigDict

class JobBase(BaseModel):
    job_type: str
    params_json: Optional[Dict[str, Any]] = None

class JobCreate(JobBase):
    pass

class JobResponse(JobBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    subject_id: int
    state: str
    progress_pct: float
    progress_message: Optional[str] = None
    log_path: Optional[str] = None
    pid: Optional[int] = None
    host: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
