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

settings = Settings()

# Ensure DATA_ROOT and SUBJECTS_DIR exist
os.makedirs(settings.DATA_ROOT, exist_ok=True)
os.makedirs(settings.SUBJECTS_DIR, exist_ok=True)
# Create raw receiving folder under DATA_ROOT/recv matching legacy path structure
os.makedirs(os.path.join(settings.DATA_ROOT, "recv"), exist_ok=True)
