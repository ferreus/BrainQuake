import os
import shutil
import time
import subprocess
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.config import settings
from app.models import Job, Subject, Artifact

def _run_subprocess_cmd(cmd, job, step_name, db, log_file, use_freesurfer_env=False):
    prefix = ""
    if use_freesurfer_env and settings.FREESURFER_HOME:
        setup_script = os.path.join(settings.FREESURFER_HOME, 'SetUpFreeSurfer.sh')
        if os.path.exists(setup_script):
            prefix = f"export FREESURFER_HOME={settings.FREESURFER_HOME} && source {setup_script} && "
        else:
            prefix = f"export FREESURFER_HOME={settings.FREESURFER_HOME} && "
            
    full_cmd = f"{prefix}{cmd}"
    log_file.write(f"\n[{datetime.now(timezone.utc)}] Starting step '{step_name}': {full_cmd}\n")
    log_file.flush()
    
    t0 = time.time()
    res = subprocess.run(full_cmd, shell=True, executable='/bin/bash', stdout=log_file, stderr=log_file)
    elapsed = time.time() - t0
    
    if res.returncode != 0:
        log_file.write(f"[{datetime.now(timezone.utc)}] Step '{step_name}' failed with status {res.returncode} after {elapsed:.1f}s\n")
        log_file.flush()
        raise RuntimeError(f"Step '{step_name}' failed with status {res.returncode}")
        
    log_file.write(f"[{datetime.now(timezone.utc)}] Step '{step_name}' completed in {elapsed:.1f}s\n")
    log_file.flush()

def register_artifact(db: Session, subject_id: int, job_id: int, kind: str, file_path: str):
    rel_path = os.path.relpath(file_path, settings.DATA_ROOT)
    artifact = Artifact(
        subject_id=subject_id,
        job_id=job_id,
        kind=kind,
        rel_path=rel_path,
        meta_json={}
    )
    db.add(artifact)
    db.commit()

def run_recon_job(db: Session, job: Job, log_file):
    subject = db.query(Subject).filter(Subject.id == job.subject_id).first()
    if not subject:
        raise ValueError("Subject not found")
        
    recon_type = (job.params_json or {}).get("recon_type", "recon-all")
    name = subject.name
    
    # 1. Setup paths
    cdir = os.path.join(settings.DATA_ROOT, "recv", name)
    os.makedirs(cdir, exist_ok=True)
    
    # Check if a zip archive was uploaded and unzip it if no T1 file is found
    zip_path = os.path.join(cdir, f"{name}.zip")
    t1_path = os.path.join(cdir, f"{name}T1.nii.gz")
    
    if os.path.exists(zip_path) and not os.path.exists(t1_path):
        job.progress_pct = 5.0
        job.progress_message = "Unzipping uploaded archive"
        db.commit()
        
        cmd_unzip = f"unzip -o {zip_path} -d {cdir}"
        _run_subprocess_cmd(cmd_unzip, job, "unzip", db, log_file)
        
    # Verify T1 file exists
    if not os.path.exists(t1_path):
        raise FileNotFoundError(f"T1 input file not found at {t1_path}. Please upload a T1 scan first.")
        
    # recon-all/fast-surfer/infant_recon_all all treat $SUBJECTS_DIR/<name> merely
    # *existing* (regardless of contents) as "this subject already has a prior run"
    # when given -i, and refuse with "You are trying to re-run an existing subject
    # with (possibly) new input data" -- FreeSurfer's own suggested fix is exactly
    # "delete the subject folder and re-run". Wipe any stale/partial directory (e.g.
    # from a previous failed attempt being retried) so every invocation gets the
    # clean slate these tools expect; do NOT recreate it here -- the recon tool
    # creates it itself for a fresh run, and pre-creating it (even empty) is what
    # caused this to fail on a genuinely first-ever run.
    subject_recon_dir = os.path.join(settings.SUBJECTS_DIR, name)
    if os.path.exists(subject_recon_dir):
        shutil.rmtree(subject_recon_dir)

    # 2. Run FreeSurfer/FastSurfer/InfantSurfer
    job.progress_pct = 10.0
    job.progress_message = f"Running {recon_type} (this can take a long time)"
    db.commit()

    if recon_type == "recon-all":
        cmd = f"recon-all -i {t1_path} -s {name} -all -parallel -openmp 8"
        _run_subprocess_cmd(cmd, job, "recon-all", db, log_file, use_freesurfer_env=True)
    elif recon_type == "fast-surfer":
        # FastSurfer-master path
        fastpath = "/home/hello/Downloads/labServer/FastSurfer-master"
        # Fallback to local script or mock if needed
        cmd = f"cd {fastpath} && ./run_fastsurfer.sh --t1 {t1_path} --sid {name}fast --sd {settings.SUBJECTS_DIR} --parallel --threads 8 --py python3 --surfreg"
        _run_subprocess_cmd(cmd, job, "fast-surfer", db, log_file, use_freesurfer_env=True)
    elif recon_type == "infant-surfer":
        cmd = f"infant_recon_all --s {name}"
        _run_subprocess_cmd(cmd, job, "infant-surfer", db, log_file, use_freesurfer_env=True)

    # fslresults is only needed later, by CT registration -- created now that the
    # recon tool owns having created SUBJECTS_DIR/<name> itself above
    os.makedirs(os.path.join(settings.SUBJECTS_DIR, name, "fslresults"), exist_ok=True)

    # 3. Post-recon steps
    job.progress_pct = 80.0
    job.progress_message = "Converting orig.mgz -> orig.nii.gz"
    db.commit()
    
    mri_dir = os.path.join(settings.SUBJECTS_DIR, name, "mri")
    orig_mgz = os.path.join(mri_dir, "orig.mgz")
    orig_nii = os.path.join(mri_dir, "orig.nii.gz")
    
    # Check if orig.mgz exists (it should after recon-all)
    if os.path.exists(orig_mgz):
        cmd_mri_convert = f"mri_convert {orig_mgz} {orig_nii}"
        _run_subprocess_cmd(cmd_mri_convert, job, "mri_convert", db, log_file, use_freesurfer_env=True)
        register_artifact(db, subject.id, job.id, "orig_nii", orig_nii)
    else:
        log_file.write(f"Warning: orig.mgz not found at {orig_mgz}; skipping convert.\n")
        
    job.progress_pct = 85.0
    job.progress_message = "Binarizing brainmask"
    db.commit()
    
    brainmask_mgz = os.path.join(mri_dir, "brainmask.mgz")
    mask_mgz = os.path.join(mri_dir, "mask.mgz")
    if os.path.exists(brainmask_mgz):
        cmd_mri_binarize = f"mri_binarize --i {brainmask_mgz} --o {mask_mgz} --min 1"
        _run_subprocess_cmd(cmd_mri_binarize, job, "mri_binarize", db, log_file, use_freesurfer_env=True)
        register_artifact(db, subject.id, job.id, "mask_mgz", mask_mgz)
        
    job.progress_pct = 90.0
    job.progress_message = "Converting annotations to labels"
    db.commit()
    
    # Annotation to labels
    cmd_label_convert_rh = f"mri_annotation2label --subject {name} --hemi rh --outdir {settings.SUBJECTS_DIR}/{name}/label"
    _run_subprocess_cmd(cmd_label_convert_rh, job, "annotation2label rh", db, log_file, use_freesurfer_env=True)
    
    cmd_label_convert_lh = f"mri_annotation2label --subject {name} --hemi lh --outdir {settings.SUBJECTS_DIR}/{name}/label"
    _run_subprocess_cmd(cmd_label_convert_lh, job, "annotation2label lh", db, log_file, use_freesurfer_env=True)
    
    # 4. Zipping results
    job.progress_pct = 95.0
    job.progress_message = "Zipping reconstruction results"
    db.commit()
    
    zip_out = os.path.join(settings.SUBJECTS_DIR, f"{name}.zip")
    cmd_zip = f"cd {settings.SUBJECTS_DIR} && zip -rq {zip_out} {name}"
    _run_subprocess_cmd(cmd_zip, job, "zip results", db, log_file)
    register_artifact(db, subject.id, job.id, "recon_zip", zip_out)
    
    # Update subject folder path in subject
    subject.subject_dir = os.path.join(settings.SUBJECTS_DIR, name)
    db.add(subject)
    db.commit()
