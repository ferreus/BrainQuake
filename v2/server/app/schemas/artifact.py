from datetime import datetime
from typing import Optional, Dict, Any
from pydantic import BaseModel, ConfigDict

class ArtifactBase(BaseModel):
    kind: str
    rel_path: str
    meta_json: Optional[Dict[str, Any]] = None

class ArtifactCreate(ArtifactBase):
    subject_id: int
    job_id: Optional[int] = None

class ArtifactResponse(ArtifactBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    subject_id: int
    job_id: Optional[int] = None
    created_at: datetime
