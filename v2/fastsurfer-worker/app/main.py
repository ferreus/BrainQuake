import os
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from app.runner import JobRunner, JobNotFound, JobAlreadyExists, AtCapacity
from app.schemas import RunRequest, RunAccepted, JobStatus

MAX_CONCURRENT = int(os.environ.get("FASTSURFER_MAX_CONCURRENT", "1"))

app = FastAPI(title="fastsurfer-worker")
runner = JobRunner(max_concurrent=MAX_CONCURRENT)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/jobs", response_model=RunAccepted, status_code=202)
def create_job(req: RunRequest):
    try:
        runner.start(req.job_id, req.t1_path, req.sid, req.sd, req.license_path, req.threads, req.device)
    except JobAlreadyExists:
        raise HTTPException(status_code=409, detail=f"job {req.job_id} already tracked")
    except AtCapacity:
        raise HTTPException(status_code=429, detail="fastsurfer-worker at capacity")
    return {"job_id": req.job_id, "state": "running"}


@app.get("/jobs/{job_id}", response_model=JobStatus)
def get_job(job_id: str):
    try:
        return runner.status(job_id)
    except JobNotFound:
        raise HTTPException(status_code=404, detail="job not found")


@app.get("/jobs/{job_id}/log", response_class=PlainTextResponse)
def get_job_log(job_id: str):
    try:
        return runner.log(job_id)
    except JobNotFound:
        raise HTTPException(status_code=404, detail="job not found")


@app.delete("/jobs/{job_id}")
def cancel_job(job_id: str):
    try:
        runner.cancel(job_id)
    except JobNotFound:
        raise HTTPException(status_code=404, detail="job not found")
    return {"message": "cancel requested"}
