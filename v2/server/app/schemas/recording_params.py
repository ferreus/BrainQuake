from datetime import datetime
from typing import Any

from pydantic import BaseModel


class RecordingParamsResponse(BaseModel):
    edf_artifact_id: int
    ictal_params: dict[str, Any] | None = None
    interictal_params: dict[str, Any] | None = None
    annotations: list[dict[str, Any]] = []
    updated_at: datetime | None = None
