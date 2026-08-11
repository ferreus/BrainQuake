from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer

from app.db import Base


class RecordingParams(Base):
    __tablename__ = "recording_params"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    edf_artifact_id = Column(
        Integer, ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    ictal_params_json = Column(JSON, nullable=True)
    interictal_params_json = Column(JSON, nullable=True)
    annotations_json = Column(JSON, nullable=True)  # list[{onset, duration, description}]
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
