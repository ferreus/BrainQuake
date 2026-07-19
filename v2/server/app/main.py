from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.db import engine, Base
from app.routers import subjects, jobs, recon, electrodes, artifacts, ictal, interictal, soz

# Automatically create tables on startup if they don't exist
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="BrainQuake v2 Server API",
    description="REST API for epilepsy surgery planning: FreeSurfer, FSL, electrode segmentation, and seizure-focus computation.",
    version="2.0.0"
)

# CORS middleware config
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Trust-network assumption
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers
app.include_router(subjects.router)
app.include_router(jobs.router)
app.include_router(recon.router)
app.include_router(electrodes.router)
app.include_router(ictal.router)
app.include_router(interictal.router)
app.include_router(soz.router)
app.include_router(artifacts.router)

@app.get("/")
def read_root():
    return {"message": "Welcome to BrainQuake v2 Server API!"}
