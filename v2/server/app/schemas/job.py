from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class JobBase(BaseModel):
    job_type: str
    params_json: dict[str, Any] | None = None

class JobCreate(JobBase):
    pass

class JobResponse(JobBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    subject_id: int
    state: str
    progress_pct: float
    progress_message: str | None = None
    log_path: str | None = None
    pid: int | None = None
    host: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
