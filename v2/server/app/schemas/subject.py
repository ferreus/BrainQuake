from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict

class SubjectBase(BaseModel):
    name: str
    recon_type: Optional[str] = None

class SubjectCreate(SubjectBase):
    pass

class SubjectResponse(SubjectBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    subject_dir: Optional[str] = None
    created_at: datetime
    updated_at: datetime
