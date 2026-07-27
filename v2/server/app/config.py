import os
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    SUBJECTS_DIR: str = Field(
        default_factory=lambda: os.getenv(
            "SUBJECTS_DIR", "./data/subjects"
        )
    )
    FREESURFER_HOME: str = Field(
        default_factory=lambda: os.getenv("FREESURFER_HOME", "./data/freesurfer")
    )
    DATA_ROOT: str = Field(default="./data")
    DB_URL: str = Field(default="sqlite:///./data/brainquake.db")
    FS_LICENSE: str = Field(
        default_factory=lambda: os.getenv("FS_LICENSE", "")
    )
    HOUGH3DLINES_BIN: str = Field(
        default_factory=lambda: os.getenv("HOUGH3DLINES_BIN", "hough3dlines")
    )

    # How many jobs (across all subjects/types except fast-surfer recon, which
    # has its own separate pool below) the worker executes at once. Jobs for
    # the same subject never run concurrently regardless of this value -- see
    # workers/jobs_worker.py's atomic per-subject claim.
    MAX_CONCURRENT_JOBS: int = Field(
        default_factory=lambda: int(os.getenv("MAX_CONCURRENT_JOBS", "2"))
    )

    # FastSurfer runs via a separate sidecar service (v2/fastsurfer-worker),
    # opt-in and disabled by default -- see app/services/fastsurfer_client.py.
    FASTSURFER_ENABLED: bool = Field(
        default_factory=lambda: os.getenv("FASTSURFER_ENABLED", "false").lower() == "true"
    )
    FASTSURFER_WORKER_URL: str = Field(
        default_factory=lambda: os.getenv("FASTSURFER_WORKER_URL", "http://fastsurfer-worker:8000")
    )
    # Separate from MAX_CONCURRENT_JOBS: FastSurfer jobs run for hours and are
    # dispatched by their own independent loop, so they never compete for or
    # block the general pool's slots.
    FASTSURFER_MAX_CONCURRENT: int = Field(
        default_factory=lambda: int(os.getenv("FASTSURFER_MAX_CONCURRENT", "1"))
    )
    FASTSURFER_POLL_INTERVAL_SECONDS: int = Field(
        default_factory=lambda: int(os.getenv("FASTSURFER_POLL_INTERVAL_SECONDS", "10"))
    )
    FASTSURFER_THREADS: int = Field(
        default_factory=lambda: int(os.getenv("FASTSURFER_THREADS", "8"))
    )
    FASTSURFER_DEVICE: str = Field(
        default_factory=lambda: os.getenv("FASTSURFER_DEVICE", "cpu")
    )

settings = Settings()

# Ensure DATA_ROOT and SUBJECTS_DIR exist
os.makedirs(settings.DATA_ROOT, exist_ok=True)
os.makedirs(settings.SUBJECTS_DIR, exist_ok=True)
# Create raw receiving folder under DATA_ROOT/recv matching legacy path structure
os.makedirs(os.path.join(settings.DATA_ROOT, "recv"), exist_ok=True)
