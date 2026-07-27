from typing import Optional
from pydantic import BaseModel


class RunRequest(BaseModel):
    job_id: str
    t1_path: str
    sid: str
    sd: str
    license_path: str
    threads: int = 8
    device: str = "cpu"


class RunAccepted(BaseModel):
    job_id: str
    state: str


class JobStatus(BaseModel):
    state: str  # "running" | "finished" | "failed"
    progress_message: Optional[str] = None
    returncode: Optional[int] = None
