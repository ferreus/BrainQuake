from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ArtifactBase(BaseModel):
    kind: str
    rel_path: str
    meta_json: dict[str, Any] | None = None

class ArtifactCreate(ArtifactBase):
    subject_id: int
    job_id: int | None = None

class ArtifactResponse(ArtifactBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    subject_id: int
    job_id: int | None = None
    created_at: datetime
