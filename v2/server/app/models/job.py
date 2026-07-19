from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship
from app.db import Base

class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    subject_id = Column(Integer, ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False)
    job_type = Column(String, nullable=False)  # recon, fastsurfer, infant_recon, ct_register, elec_detect, elec_segment, etc.
    state = Column(String, default="queued", nullable=False)  # queued, running, finished, failed, cancelled
    progress_pct = Column(Float, default=0.0, nullable=False)
    progress_message = Column(String, nullable=True)
    params_json = Column(JSON, nullable=True)
    log_path = Column(String, nullable=True)
    pid = Column(Integer, nullable=True)
    host = Column(String, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    subject = relationship("Subject", back_populates="jobs")
    artifacts = relationship("Artifact", back_populates="job")
