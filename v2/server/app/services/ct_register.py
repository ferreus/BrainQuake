import os
import time
import subprocess
import nibabel as nib
import numpy as np
from datetime import datetime, timezone
from scipy.ndimage import binary_erosion
from sqlalchemy.orm import Session
from app.config import settings
from app.models import Job, Subject, Artifact
from app.services.recon import _run_subprocess_cmd, register_artifact

def run_ct_register_job(db: Session, job: Job, log_file):
    subject = db.query(Subject).filter(Subject.id == job.subject_id).first()
    if not subject:
        raise ValueError("Subject not found")

    patient = subject.name

    ct_dir = os.path.join(settings.DATA_ROOT, "recv", patient)
    mri_dir = os.path.join(settings.SUBJECTS_DIR, patient, "mri")

    # 1. Verify files exist
    ct_path = os.path.join(ct_dir, f"{patient}CT.nii.gz")
    if not os.path.exists(ct_path):
        raise FileNotFoundError(f"CT scan file not found at {ct_path}. Please upload a CT scan first.")

    orig_mgz = os.path.join(mri_dir, "orig.mgz")
    if not os.path.exists(orig_mgz):
        raise FileNotFoundError(f"FreeSurfer orig.mgz not found at {orig_mgz}. Please run reconstruction first.")

    # Ensure folders exist
    fslresults_dir = os.path.join(ct_dir, "fslresults")
    os.makedirs(fslresults_dir, exist_ok=True)

    # 2. mri_convert (if orig.nii.gz doesn't exist)
    orig_nii = os.path.join(mri_dir, "orig.nii.gz")
    if not os.path.exists(orig_nii):
        job.progress_pct = 10.0
        job.progress_message = "Converting orig.mgz -> orig.nii.gz"
        db.commit()
        cmd_convert = f"mri_convert {orig_mgz} {orig_nii}"
        _run_subprocess_cmd(cmd_convert, job, "mri_convert", db, log_file, use_freesurfer_env=True)
        register_artifact(db, subject.id, job.id, "orig_nii", orig_nii)

    # 3. flirt CT-to-MRI Registration
    job.progress_pct = 30.0
    job.progress_message = "Registering CT to MRI using FSL flirt"
    db.commit()

    xfm_path = os.path.join(fslresults_dir, f"{patient}invol2refvol.mat")
    out_path = os.path.join(fslresults_dir, f"{patient}outvol.nii.gz")

    # Run flirt command with standard params
    cmd_flirt = (
        f"flirt -in {ct_path} -ref {orig_nii} -omat {xfm_path} -out {out_path} "
        f"-dof 12 -bins 256 -cost normmi -searchrx -180 180 -searchry -180 180 -searchrz -180 180"
    )
    _run_subprocess_cmd(cmd_flirt, job, "flirt CT-to-MRI registration", db, log_file)
    register_artifact(db, subject.id, job.id, "ct_reg_mat", xfm_path)
    register_artifact(db, subject.id, job.id, "ct_reg_nii", out_path)

    # 4. Masking step
    job.progress_pct = 80.0
    job.progress_message = "Eroding mask and isolating intracranial CT"
    db.commit()

    mask_mgz = os.path.join(mri_dir, "mask.mgz")
    if not os.path.exists(mask_mgz):
        # Generate mask.mgz if not present
        brainmask_mgz = os.path.join(mri_dir, "brainmask.mgz")
        if not os.path.exists(brainmask_mgz):
            raise FileNotFoundError(f"Neither mask.mgz nor brainmask.mgz found in {mri_dir}")
        cmd_mri_binarize = f"mri_binarize --i {brainmask_mgz} --o {mask_mgz} --min 1"
        _run_subprocess_cmd(cmd_mri_binarize, job, "mri_binarize", db, log_file, use_freesurfer_env=True)
        register_artifact(db, subject.id, job.id, "mask_mgz", mask_mgz)

    log_file.write(f"\n[{datetime.now(timezone.utc)}] Loading registered CT and mask files for brain extraction...\n")
    log_file.flush()

    img_ct = nib.load(out_path)
    img_mask = nib.load(mask_mgz)

    data_ct = img_ct.get_fdata()
    data_mask = img_mask.get_fdata()

    log_file.write(f"[{datetime.now(timezone.utc)}] Performing binary erosion on brain mask (10 iterations)...\n")
    log_file.flush()
    data_mask_ero = binary_erosion(data_mask, iterations=10)

    # Mask out non-brain tissue and clamp negative values
    data_ct[data_mask_ero == 0] = 0
    data_ct[data_ct < 0] = 0

    intracranial_path = os.path.join(fslresults_dir, f"{patient}intracranial.nii.gz")
    img_out = nib.Nifti1Image(data_ct, img_ct.affine)
    nib.save(img_out, intracranial_path)

    log_file.write(f"[{datetime.now(timezone.utc)}] Saved intracranial CT at {intracranial_path}\n")
    log_file.flush()

    register_artifact(db, subject.id, job.id, "ct_intracranial_nii", intracranial_path)

    # Also register results under legacy subject folder if needed
    legacy_fsl_dir = os.path.join(settings.SUBJECTS_DIR, patient, "fslresults")
    os.makedirs(legacy_fsl_dir, exist_ok=True)
    legacy_out = os.path.join(legacy_fsl_dir, f"{patient}CT_Reg.nii.gz")
    import shutil
    shutil.copy2(out_path, legacy_out)
    log_file.write(f"[{datetime.now(timezone.utc)}] Copied registered CT to legacy subject dir: {legacy_out}\n")
    log_file.flush()
