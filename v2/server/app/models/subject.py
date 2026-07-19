from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.orm import relationship
from app.db import Base

class Subject(Base):
    __tablename__ = "subjects"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String, unique=True, index=True, nullable=False)
    hospital = Column(String, nullable=True)
    recon_type = Column(String, nullable=True)  # recon-all, fast-surfer, infant-surfer
    subject_dir = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    jobs = relationship("Job", back_populates="subject", cascade="all, delete-orphan")
    artifacts = relationship("Artifact", back_populates="subject", cascade="all, delete-orphan")
