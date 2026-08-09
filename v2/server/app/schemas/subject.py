from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SubjectBase(BaseModel):
    name: str
    recon_type: str | None = None

class SubjectCreate(SubjectBase):
    pass

class SubjectResponse(SubjectBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    subject_dir: str | None = None
    created_at: datetime
    updated_at: datetime
