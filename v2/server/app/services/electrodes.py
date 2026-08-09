import csv
import io
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile
from urllib.parse import unquote

import h5py
import nibabel as nib
import numpy as np
from scipy.ndimage import binary_erosion
from sklearn.linear_model import Lasso, LinearRegression
from sklearn.mixture import GaussianMixture as GMM
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Artifact, Job, Subject
from app.services.job_control import check_cancelled, run_and_track_subprocess
from app.services.recon import register_artifact

# Ported from utils/elec_utils.py (git tag legacy-final). Split into two job
# types: detect() (Preprocess_thread + GenerateLabel_thread -- hough3dlines + GMM
# clustering, producing a voxel-labeled volume) and segment() (ContactSegment_thread
# -- per-contact convergence via ElectrodeSeg, producing final contact coordinates).
# The GMM label review step in between is `commit_labels` (PUT .../labels), a new
# endpoint that didn't exist in the legacy single-process GUI flow.


def _patient_dirs(subject: Subject):
    """Mirrors the legacy app's directory convention: directory_ct = <subject>/fslresults
    (where CT_Reg.nii.gz already lives, written by ct_register.py), directory_surf =
    <subject> (the FreeSurfer subject dir, for mri/mask.mgz)."""
    surf_dir = os.path.join(settings.SUBJECTS_DIR, subject.name)
    ct_dir = os.path.join(surf_dir, "fslresults")
    mri_dir = os.path.join(surf_dir, "mri")
    return surf_dir, ct_dir, mri_dir


def _run_hough3dlines(cmd, log_file=None, job=None, db=None):
    t0 = time.time()
    if job is not None and db is not None:
        result = run_and_track_subprocess(cmd, job, db, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    else:
        # No job context (e.g. a standalone/offline script) -- nothing to track a pid onto.
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    elapsed = time.time() - t0
    if log_file:
        log_file.write(f"Running: {cmd}\n")
        if result.stdout:
            log_file.write(f"stdout: {result.stdout.strip()}\n")
        if result.stderr:
            log_file.write(f"stderr: {result.stderr.strip()}\n")
        log_file.write(f"Finished in {elapsed:.1f}s with return code {result.returncode}\n")
        log_file.flush()
    return result


def dataExtraction(intraFile, thre=0.2):
    rawData = nib.load(intraFile).get_fdata()
    maxVal = np.amax(rawData)
    thre = maxVal * thre
    threData = np.copy(rawData)
    threData[threData < thre] = 0
    xs, ys, zs = np.where(threData != 0)
    return xs, ys, zs


def trackRecognition(patient, cmd_hough3d, CTresult_dir, intraFile, log_file, thre=0.2, job=None, db=None):
    xs, ys, zs = dataExtraction(intraFile, thre)

    X = np.transpose(np.array((xs, ys, zs)))
    fname = os.path.join(CTresult_dir, f"{patient}_3dPointClouds.dat")
    np.savetxt(fname, X, fmt='%.4f', delimiter=',', newline='\n', header='point clouds', footer='', comments='# ')

    outfile = os.path.join(CTresult_dir, f"{patient}.txt")
    cmd_hough = f"{cmd_hough3d} -o {outfile} -minvotes 5 {fname}"
    _run_hough3dlines(cmd_hough, log_file, job=job, db=db)
    return xs, ys, zs, fname, outfile


def preprocess_ct(patient, ct_dir, mri_dir, K, thre_pct, ero_itr):
    """Port of Preprocess_thread.run(). thre_pct is 0-100; K/ero_itr are baked into
    the intracranial filename so a re-run with different params doesn't clobber a
    previous one, matching the legacy convention."""
    mask_file = os.path.join(mri_dir, "mask.mgz")
    if not os.path.exists(mask_file):
        raise FileNotFoundError(f"{mask_file} not found. Run reconstruction first.")
    data_mask = nib.load(mask_file).get_fdata()
    data_mask_ero = binary_erosion(data_mask, iterations=ero_itr)

    CTreg_file = os.path.join(ct_dir, f"{patient}CT_Reg.nii.gz")
    if not os.path.exists(CTreg_file):
        raise FileNotFoundError(f"{CTreg_file} not found. Run CT registration first.")
    img_ct = nib.load(CTreg_file)
    data_ct = img_ct.get_fdata()
    maxVal = np.amax(data_ct)
    thre = thre_pct / 100
    thre_val = maxVal * thre

    data_ct[data_mask_ero == 0] = 0
    img1 = nib.Nifti1Image(data_ct, img_ct.affine)
    intra_file1 = os.path.join(ct_dir, f"{patient}CT_intra.nii.gz")
    nib.save(img1, intra_file1)

    data_ct = data_ct.copy()
    data_ct[data_ct < thre_val] = 0
    img0 = nib.Nifti1Image(data_ct, img_ct.affine)
    intra_file = os.path.join(ct_dir, f"{patient}CT_intracranial_{thre}_{K}_{ero_itr}.nii.gz")
    nib.save(img0, intra_file)

    return intra_file1, intra_file


def generate_labels(patient, ct_dir, intra_file, K, log_file, job=None, db=None):
    """Port of GenerateLabel_thread.run(): hough3dlines line detection -> keep the K
    best-supported tracks as GMM centroids -> per-voxel cluster labels -> Labels.npy.
    Raises RuntimeError if fewer than K tracks were detected (mirrors the legacy
    thread's finished.emit(1) failure path, which produced no labels.npy)."""
    xs, ys, zs, cloud_file, hough_file = trackRecognition(
        patient=patient, cmd_hough3d=settings.HOUGH3DLINES_BIN, CTresult_dir=ct_dir,
        intraFile=intra_file, log_file=log_file, thre=0, job=job, db=db)

    elec_track = []
    with open(hough_file) as f:
        for line in f.readlines():
            a = re.findall(r"\d+\.?\d*", line)
            a = [float(x) for x in a]
            elec_track.append(a)
    elec_track = np.array(elec_track)
    K_check = elec_track.shape[0]

    if K_check < K:
        raise RuntimeError(
            f"Only {K_check} tracks were detected by hough3dlines, but {K} electrodes "
            f"were requested. Try a different threshold/erosion.")

    # column 0 is npoints (track support); pick the K best-supported tracks as GMM
    # centroids rather than assuming file order, since a well-defined electrode has
    # more Hough-clustered points than a noisy fragment.
    best_order = np.argsort(-elec_track[:, 0])[:K]
    centroids = np.array(elec_track[best_order, 1:4])
    X = np.transpose(np.vstack((xs, ys, zs)))
    gmm = GMM(n_components=K, covariance_type='full', means_init=centroids, random_state=None).fit(X)
    labels = gmm.predict(X)

    Labels = np.zeros((256, 256, 256))
    for i in range(K):
        ind = np.where(labels == i)
        Labels[xs[ind], ys[ind], zs[ind]] = i + 1

    labels_path = os.path.join(ct_dir, f"{patient}_labels.npy")
    np.save(labels_path, Labels, allow_pickle=True)
    return labels_path, K_check


def run_elec_detect_job(db: Session, job: Job, log_file):
    subject = db.query(Subject).filter(Subject.id == job.subject_id).first()
    if not subject:
        raise ValueError("Subject not found")

    params = job.params_json or {}
    K = int(params["K"])
    thre_pct = float(params["threshold_pct"])
    ero_itr = int(params["erosion_iterations"])

    surf_dir, ct_dir, mri_dir = _patient_dirs(subject)
    os.makedirs(ct_dir, exist_ok=True)

    job.progress_pct = 10.0
    job.progress_message = "Eroding brain mask and thresholding intracranial CT"
    db.commit()
    intra_file1, intra_file = preprocess_ct(subject.name, ct_dir, mri_dir, K, thre_pct, ero_itr)
    register_artifact(db, subject.id, job.id, "ct_intra_nii", intra_file1)
    register_artifact(db, subject.id, job.id, "ct_intracranial_nii", intra_file)

    check_cancelled(db, job)
    job.progress_pct = 50.0
    job.progress_message = "Running hough3dlines + GMM clustering"
    db.commit()
    labels_path, K_check = generate_labels(subject.name, ct_dir, intra_file, K, log_file, job=job, db=db)
    register_artifact(db, subject.id, job.id, "labels_npy", labels_path)

    job.progress_pct = 95.0
    job.progress_message = f"Detected {K_check} tracks, clustered into {K} electrodes"
    db.commit()


def summarize_labels(subject: Subject):
    """GET .../electrodes/labels-summary: cheap per-cluster stats (voxel count
    + centroid) computed server-side from the labels volume, so a label-review/
    exclude UI can be built without ever shipping the full 256^3 label volume
    (Labels.npy, ~128MB as float64) to the browser -- only chn-xyz/contacts
    (final segmented contact coordinates) were JSON-ready before this; nothing
    exposed the intermediate GMM clusters the legacy app's cluster-preview
    matplotlib scatter showed.

    The centroid is voxel-index space (mean of np.where(Labels == v)), which
    is NOT the space chn-xyz/contacts are in -- ElectrodeSeg.resulting() maps
    voxel (vx, vy, vz) -> (128-vx, vz-128, 128-vy) before ever writing a
    result .txt. Apply the same map here so a client can plot cluster
    centroids directly alongside the brain surface and segmented contacts
    without knowing about voxel space at all."""
    _, ct_dir, _ = _patient_dirs(subject)
    labels_path = os.path.join(ct_dir, f"{subject.name}_labels.npy")
    if not os.path.exists(labels_path):
        raise FileNotFoundError(f"{labels_path} not found. Run detect() first.")

    Labels = np.load(labels_path)
    values = sorted(v for v in np.unique(Labels) if v != 0)
    clusters = []
    for v in values:
        idx = np.where(Labels == v)
        vx, vy, vz = float(np.mean(idx[0])), float(np.mean(idx[1])), float(np.mean(idx[2]))
        clusters.append({
            "label": int(v),
            "voxel_count": int(len(idx[0])),
            "centroid": [128.0 - vx, vz - 128.0, 128.0 - vy],
        })
    return {"K": len(values), "clusters": clusters}


def commit_labels(subject: Subject, exclude_labels):
    """PUT .../labels: drop reviewed-out clusters (e.g. noise tracks the GMM
    mistook for an electrode) and renumber the remaining label values contiguously
    1..K' so segment()'s alphabetic naming (ElectrodeSeg) stays gap-free. There is
    no per-voxel edit in the legacy app either -- only whole-cluster accept/reject."""
    _, ct_dir, _ = _patient_dirs(subject)
    labels_path = os.path.join(ct_dir, f"{subject.name}_labels.npy")
    if not os.path.exists(labels_path):
        raise FileNotFoundError(f"{labels_path} not found. Run detect() first.")

    Labels = np.load(labels_path)
    exclude_set = set(exclude_labels or [])
    for v in exclude_set:
        Labels[Labels == v] = 0

    remaining = sorted(v for v in np.unique(Labels) if v != 0)
    renumbered = np.zeros_like(Labels)
    for new_val, old_val in enumerate(remaining, start=1):
        renumbered[Labels == old_val] = new_val

    np.save(labels_path, renumbered, allow_pickle=True)
    return len(remaining)


class ElectrodeSeg:
    """Near-verbatim port of utils/elec_utils.py's ElectrodeSeg -- per-contact
    centroid convergence walking out along an electrode shaft from its entry point.
    Only the constructor's file-discovery changed (direct path construction instead
    of os.walk+regex, since the v2 service controls naming precisely)."""

    def __init__(self, ct_dir, patient, iLabel, numMax, diameterSize, spacing, gap):
        self.filePath = ct_dir
        self.patientName = patient

        self.rawDataPath = os.path.join(ct_dir, f"{patient}CT_intra.nii.gz")
        if not os.path.exists(self.rawDataPath):
            raise FileNotFoundError(f"{self.rawDataPath} not found. Run detect() first.")
        self.labelsPath = os.path.join(ct_dir, f"{patient}_labels.npy")
        if not os.path.exists(self.labelsPath):
            raise FileNotFoundError(f"{self.labelsPath} not found. Run detect() first.")

        self.rawData = nib.load(self.rawDataPath).get_fdata()
        self.labels = np.load(self.labelsPath)
        self.iLabel = iLabel
        self.numMax = numMax
        self.diameterSize = diameterSize
        self.spacing = spacing
        self.gap = gap

        self.affine = nib.load(self.rawDataPath).affine
        self.inv_vox2ras_tkr = np.array([[-1, 0, 0, 128], [0, 0, -1, 128], [0, 1, 0, 128], [0, 0, 0, 1]], dtype=np.float32)

        self.labelValues = np.unique(self.labels)
        self.numElecs = len(self.labelValues) - 1
        if self.numElecs > 8:  # remove 'I' from the alphabet list, a trivial custom not to name the electrode 'I'
            self.alphaList = [chr(i) for i in range(65, 66 + self.numElecs)]
            self.alphaList.pop(8)
        else:
            self.alphaList = [chr(i) for i in range(65, 65 + self.numElecs)]
        self.iValue = self.labelValues[self.iLabel]
        self.nameLabel = self.alphaList[self.iLabel - 1]
        data_elec = np.copy(self.labels)
        data_elec[np.where(self.labels != self.iValue)] = 0
        self.xs, self.ys, self.zs = np.where(data_elec != 0)
        self.pos_elec = np.transpose(np.vstack((self.xs, self.ys, self.zs)))
        data_elec1 = np.copy(self.labels)
        data_elec1[np.where(self.labels == self.iValue)] = 0
        self.xrest, self.yrest, self.zrest = np.where(data_elec1 != 0)
        self.rawData[self.xrest, self.yrest, self.zrest] = 0
        self.rawData_single = self.rawData
        xmin = np.amin(self.xs)
        xmax = np.amax(self.xs)
        ymin = np.amin(self.ys)
        ymax = np.amax(self.ys)
        zmin = np.amin(self.zs)
        zmax = np.amax(self.zs)
        self.rawData_single[xmin:xmax + 1, ymin:ymax + 1, zmin:zmax + 1] = \
            self.rawData_single[xmin:xmax + 1, ymin:ymax + 1, zmin:zmax + 1] * 3

        self.resultPath = os.path.join(self.filePath, f"{self.patientName}_result")
        os.makedirs(self.resultPath, exist_ok=True)
        self.resultFile = os.path.join(self.resultPath, f"{self.nameLabel}.txt")
        self.elecPos = [0, 0, 0]
        self.headStart = [0, 0, 0]
        self.targetPoint = [0, 0, 0]
        self.regressInfo = [0, 0, 0, 0]

    def pipeline(self):
        self.startPoint()
        self.contactPoint(1)
        self.regression()
        for j in np.arange(self.numMax - 1):
            if int(self.elecPos[-1, 0]) == int(self.elecPos[-2, 0]) and \
               int(self.elecPos[-1, 1]) == int(self.elecPos[-2, 1]) and \
               int(self.elecPos[-1, 2]) == int(self.elecPos[-2, 2]):
                self.elecPos = self.elecPos[0:-1, :]
                break
            self.step()
            if self.flag_step_stop:
                break
        self.elecPos = self.elecPos[1:, :]
        self.resulting()

    def resulting(self):
        self.elecPos_true = np.copy(self.elecPos)
        self.elecPos_true[:, 0] = 128 - self.elecPos[:, 0]
        self.elecPos_true[:, 1] = 128 - self.elecPos[:, 1]
        self.elecPos_true[:, 2] = self.elecPos[:, 2] - 128
        self.elecPos_true = self.elecPos_true[:, [0, 2, 1]]

        self.elecFilepath = os.path.join(self.filePath, f"{self.patientName}_result")
        os.makedirs(self.elecFilepath, exist_ok=True)
        self.elecFile = os.path.join(self.elecFilepath, f"{self.nameLabel}.txt")
        with open(self.elecFile, "ab") as f:
            f.seek(0)
            f.truncate()
            np.savetxt(f, self.elecPos_true, fmt='%10.8f', delimiter=' ', newline='\n', header=f"{self.elecPos_true.shape[0]}")

        # freeview-space export -- visualization-only, kept for parity with the legacy app
        tmp = np.matmul(self.affine, self.inv_vox2ras_tkr)
        tmp1 = np.matmul(tmp, np.transpose(np.column_stack((self.elecPos_true, np.ones((self.elecPos_true.shape[0],))))))
        self.elecPos_freeview = np.transpose(tmp1)[:, 0:3]

        self.elecFilepath_freeview = os.path.join(self.filePath, f"{self.patientName}_freeview_result")
        os.makedirs(self.elecFilepath_freeview, exist_ok=True)
        self.elecFile_freeview = os.path.join(self.elecFilepath_freeview, f"{self.nameLabel}.txt")
        with open(self.elecFile_freeview, "ab") as f:
            f.seek(0)
            f.truncate()
            np.savetxt(f, self.elecPos_freeview, fmt='%10.8f', delimiter=' ', newline='\n', header=f"{self.elecPos_freeview.shape[0]}")

    def startPoint(self):
        x = [np.max(self.xs), np.min(self.xs)]
        y = [np.max(self.ys), np.min(self.ys)]
        z = [np.max(self.zs), np.min(self.zs)]
        self.reg1 = LinearRegression().fit(X=self.xs.reshape(-1, 1), y=self.ys)
        self.reg2 = LinearRegression().fit(X=self.xs.reshape(-1, 1), y=self.zs)
        self.reg3 = LinearRegression().fit(X=self.ys.reshape(-1, 1), y=self.zs)

        coefs = [abs(self.reg1.coef_), abs(self.reg2.coef_), abs(self.reg3.coef_)]
        coef_min = coefs.index(min(coefs))
        if coef_min == 0:
            index = [0 if self.reg2.coef_ > 0 else 1, 0 if self.reg3.coef_ > 0 else 1, 0]
        elif coef_min == 1:
            index = [0 if self.reg1.coef_ > 0 else 1, 0, 0 if self.reg3.coef_ > 0 else 1]
        else:
            index = [0, 0 if self.reg1.coef_ > 0 else 1, 0 if self.reg2.coef_ > 0 else 1]
        indexreverse = [~index[0], ~index[1], ~index[2]]

        point1 = np.array([x[index[0]], y[index[1]], z[index[2]]])
        point2 = np.array([x[indexreverse[0]], y[indexreverse[1]], z[indexreverse[2]]])
        center = 127.5 * np.ones(3)
        diff1 = point1 - center
        diff2 = point2 - center
        headStart = point2 if np.sum(np.transpose(diff1) * diff1) > np.sum(np.transpose(diff2) * diff2) else point1
        self.direction = indexreverse if np.sum(np.transpose(diff1) * diff1) > np.sum(np.transpose(diff2) * diff2) else index

        diffs = self.pos_elec - headStart
        diffs2 = np.power(diffs[:, 0], 2) + np.power(diffs[:, 1], 2) + np.power(diffs[:, 2], 2)
        headPointPos = np.argmin(diffs2)
        self.headStart = self.pos_elec[headPointPos, :]

    def converge(self, x, y, z):
        n = self.diameterSize
        delta = math.ceil(round((n - 1) / 2, 1))
        seq_s = np.arange(x - delta, x + delta + 1)
        seq_r = np.arange(y - delta, y + delta + 1)
        seq_c = np.arange(z - delta, z + delta + 1)

        if not ((np.array(seq_s) > 0).all() and (np.array(seq_r) > 0).all() and (np.array(seq_c) > 0).all()):
            return 0, 0, 0
        elif not ((np.array(seq_s) < 256).all() and (np.array(seq_r) < 256).all() and (np.array(seq_c) < 256).all()):
            return 0, 0, 0
        else:
            matrixVoxels = self.rawData_local[seq_s[0]:seq_s[-1] + 1, seq_r[0]:seq_r[-1] + 1, seq_c[0]:seq_c[-1] + 1]
            if np.sum(matrixVoxels) == 0:
                return 0, 0, 0
            else:
                f = np.zeros((1, 4))
                for index, element in np.ndenumerate(matrixVoxels):
                    x, y, z = index
                    tmp = np.array([x + seq_s[0], y + seq_r[0], z + seq_c[0], element])
                    f = np.vstack((f, tmp))
                f = f[1:]
                CM = np.average(f[:, :3], axis=0, weights=f[:, 3])
                return CM[0], CM[1], CM[2]

    def contactPoint(self, target):
        x0 = self.headStart[0] if target == 1 else self.x0
        y0 = self.headStart[1] if target == 1 else self.y0
        z0 = self.headStart[2] if target == 1 else self.z0

        x = int(round(x0))
        y = int(round(y0))
        z = int(round(z0))

        self.rawData_local = self.rawData_single
        diff_array = self.pos_elec - np.array([x0, y0, z0])
        elec_diffs = np.sqrt(np.dot(diff_array, np.transpose(diff_array)).diagonal())
        ind_diffs = np.where(elec_diffs <= 2)
        self.rawData_local[self.xs[ind_diffs], self.ys[ind_diffs], self.zs[ind_diffs]] = \
            self.rawData_local[self.xs[ind_diffs], self.ys[ind_diffs], self.zs[ind_diffs]] * 2
        (x1, y1, z1) = self.converge(x, y, z)
        itr = 1
        flag_convergence = 0
        while not ((x == int(round(x1))) and (y == int(round(y1))) and (z == int(round(z1)))):
            x = int(round(x1))
            y = int(round(y1))
            z = int(round(z1))
            (x1, y1, z1) = self.converge(x, y, z)
            itr = itr + 1
            if itr > 5:
                flag_convergence = 1
                break

        self.flag_step_stop = 0
        if (x1, y1, z1) == (0, 0, 0):
            self.flag_step_stop = 1
        else:
            self.targetPoint = [x1, y1, z1] if target == 1 else self.targetPoint
            self.elecPos = np.vstack([self.elecPos, [x1, y1, z1]])

    def regression(self):
        X = np.transpose(np.vstack((self.xs, self.ys)))
        y = self.zs

        forcedX = np.transpose(np.array([self.targetPoint[0], self.targetPoint[1]]))
        forcedy = self.targetPoint[2]

        X = X - forcedX
        y = y - forcedy
        reg = Lasso(fit_intercept=False).fit(X=X, y=y)
        reg.intercept_ = reg.intercept_ + forcedy - np.dot(forcedX, reg.coef_)
        reg2 = LinearRegression(fit_intercept=True).fit(X=self.xs.reshape(-1, 1), y=self.ys)

        self.coef = reg.coef_
        self.intercept = reg.intercept_
        self.coef2 = reg2.coef_
        self.intercept2 = reg2.intercept_

    def step(self):
        dis = self.spacing
        diff_x = np.max(self.xs) - np.min(self.xs)
        diff_y = np.max(self.ys) - np.min(self.ys)
        diff_z = np.max(self.zs) - np.min(self.zs)
        a = np.power(diff_x, 2) + np.power(diff_y, 2) + np.power(diff_z, 2)
        delta_x = diff_x * np.sqrt(np.power(dis, 2) / a)
        delta_y = diff_y * np.sqrt(np.power(dis, 2) / a)
        delta_z = diff_z * np.sqrt(np.power(dis, 2) / a)

        self.x0 = int(self.elecPos[-1, 0] - np.round(delta_x)) if ((self.direction[0] == -2) or (self.direction[0] == 0)) else int(self.elecPos[-1, 0] + np.round(delta_x))
        self.y0 = int(self.elecPos[-1, 1] - np.round(delta_y)) if ((self.direction[1] == -2) or (self.direction[1] == 0)) else int(self.elecPos[-1, 1] + np.round(delta_y))
        self.z0 = int(self.elecPos[-1, 2] - np.round(delta_z)) if ((self.direction[2] == -2) or (self.direction[2] == 0)) else int(self.elecPos[-1, 2] + np.round(delta_z))

        self.contactPoint(0)


def savenpy(ct_dir, patient):
    """Port of elec_utils.savenpy(): build chnXyzDict.npy from every per-electrode
    <label>.txt file under <ct_dir>/<patient>_result/."""
    result_dir = os.path.join(ct_dir, f"{patient}_result")
    elec_dict = {}
    for root, dirs, files in os.walk(result_dir, topdown=True):
        if '.DS_Store' in files:
            files.remove('.DS_Store')
        if 'chnXyzDict.npy' in files:
            files.remove('chnXyzDict.npy')
        for file in files:
            elec_name = file.split('.')[0]
            elec_info = np.atleast_2d(np.loadtxt(os.path.join(root, file)))
            elec_dict[elec_name] = elec_info

    out_path = os.path.join(ct_dir, "chnXyzDict.npy")
    np.save(out_path, elec_dict)
    return out_path


def run_elec_segment_job(db: Session, job: Job, log_file):
    subject = db.query(Subject).filter(Subject.id == job.subject_id).first()
    if not subject:
        raise ValueError("Subject not found")

    params = job.params_json or {}
    numMax = int(params.get("numMax", 20))
    diameterSize = float(params.get("diameterSize", 2.5))
    spacing = float(params.get("spacing", 2.5))
    gap = float(params.get("gap", 0))

    _, ct_dir, _ = _patient_dirs(subject)
    labels_path = os.path.join(ct_dir, f"{subject.name}_labels.npy")
    if not os.path.exists(labels_path):
        raise FileNotFoundError(f"{labels_path} not found. Run detect() (and optionally commit labels) first.")

    K = len(np.unique(np.load(labels_path))) - 1
    if K <= 0:
        raise RuntimeError("No electrode labels found -- did detect()/labels review leave any clusters?")

    for i in range(K):
        check_cancelled(db, job)
        iLabel = i + 1
        seg = ElectrodeSeg(ct_dir=ct_dir, patient=subject.name, iLabel=iLabel,
                            numMax=numMax, diameterSize=diameterSize, spacing=spacing, gap=gap)
        seg.pipeline()
        log_file.write(f"Segmented electrode {seg.nameLabel} ({seg.elecPos.shape[0]} contacts)\n")
        log_file.flush()
        job.progress_pct = 10.0 + 80.0 * (i + 1) / K
        job.progress_message = f"Segmented electrode {seg.nameLabel} ({i + 1}/{K})"
        db.commit()

    chn_xyz_path = savenpy(ct_dir, subject.name)
    register_artifact(db, subject.id, job.id, "chnXyzDict", chn_xyz_path)

    result_dir = os.path.join(ct_dir, f"{subject.name}_result")
    for fname in os.listdir(result_dir):
        register_artifact(db, subject.id, job.id, "contact_txt", os.path.join(result_dir, fname))


CONTACT_CSV_REQUIRED_COLUMNS = ("electrode", "contact_index", "surfR", "surfA", "surfS")


def parse_contacts_csv(csv_text: str):
    """Parses the electrode/contact_index/surfR/surfA/surfS CSV produced by
    Steps 3-4 of docs/seeg_slicer_contact_import_plan.md into the contacts
    list import_contacts() expects. Raises ValueError with a message meant to
    be read as a job's progress_message -- deliberately called from inside
    run_elec_import_job (not the router), so a malformed file still produces
    a normal queued-then-failed job with a clear reason, the same as every
    other pipeline step, instead of a client-side-only error with no job ever
    created."""
    text = csv_text.lstrip("﻿")  # tolerate a UTF-8 BOM from Excel exports
    reader = csv.DictReader(io.StringIO(text))
    fieldnames = [f.strip() for f in (reader.fieldnames or [])]
    missing = [c for c in CONTACT_CSV_REQUIRED_COLUMNS if c not in fieldnames]
    if missing:
        raise ValueError(
            f"CSV is missing column(s): {', '.join(missing)} "
            f"(found: {', '.join(fieldnames) or '<empty header>'})")

    contacts = []
    for i, row in enumerate(reader, start=2):  # row 1 is the header
        row = {(k.strip() if k else k): v for k, v in row.items()}
        try:
            contacts.append({
                "electrode": row["electrode"].strip(),
                "contact_index": int(float(row["contact_index"])),
                "x": float(row["surfR"]),
                "y": float(row["surfA"]),
                "z": float(row["surfS"]),
            })
        except (TypeError, ValueError, AttributeError) as e:
            raise ValueError(f"row {i}: could not parse {row!r} ({e})")

    if not contacts:
        raise ValueError("CSV has no data rows")
    return contacts


def _validate_contiguous_indices(contacts):
    """Shared by import_contacts() and parse_mrb() -- for the latter, this
    lets a gap fail the preview job outright instead of only surfacing once
    someone clicks Approve."""
    by_electrode = {}
    for c in contacts:
        by_electrode.setdefault(c["electrode"], []).append(c["contact_index"])
    for electrode, indices in by_electrode.items():
        indices_sorted = sorted(indices)
        if indices_sorted != list(range(1, len(indices_sorted) + 1)):
            raise ValueError(
                f"Electrode {electrode!r} contact_index values are not contiguous "
                f"1..N (got {indices_sorted}) -- chn-xyz/soz.py assume row order == contact "
                f"number, so a gap would silently mislabel every contact after it.")


def import_contacts(subject: Subject, contacts):
    """Writes the same `<label>.txt` + `chnXyzDict.npy` artifacts segment()
    produces, but from an externally-resolved contact list (e.g. from a 3D
    Slicer `.mrb` -- see docs/seeg_slicer_contact_import_plan.md, Steps 1-5)
    instead of hough3dlines/GMM/ElectrodeSeg. Bypasses ct_register/detect/
    segment entirely -- no `CT_Reg.nii.gz` or `labels_npy` is required, only
    a FreeSurfer recon (`_patient_dirs` still points at
    `<SUBJECTS_DIR>/<patient>/fslresults`, same place segment() writes to, so
    chn-xyz/contacts/soz_fuse all work unmodified on the result).

    contacts: list of {"electrode": str, "contact_index": int (1-based),
    "x": float, "y": float, "z": float}. Coordinates must already be in
    FreeSurfer surface (tkreg) RAS -- the space `ElectrodeSeg.resulting()`
    writes to `<label>.txt` (voxel (vx,vy,vz) -> (128-vx, vz-128, 128-vy) for
    a 256^3 1mm conform volume), since that's what chn-xyz/contacts and
    soz.py's fusion expect.
    """
    _validate_contiguous_indices(contacts)

    by_electrode = {}
    for c in contacts:
        by_electrode.setdefault(c["electrode"], []).append(c)

    _, ct_dir, _ = _patient_dirs(subject)
    result_dir = os.path.join(ct_dir, f"{subject.name}_result")
    os.makedirs(result_dir, exist_ok=True)

    written = []
    for electrode, pts in by_electrode.items():
        pts_sorted = sorted(pts, key=lambda c: c["contact_index"])
        arr = np.array([[p["x"], p["y"], p["z"]] for p in pts_sorted])
        out_file = os.path.join(result_dir, f"{electrode}.txt")
        with open(out_file, "ab") as f:
            f.seek(0)
            f.truncate()
            np.savetxt(f, arr, fmt='%10.8f', delimiter=' ', newline='\n', header=f"{arr.shape[0]}")
        written.append(out_file)

    chn_xyz_path = savenpy(ct_dir, subject.name)
    return chn_xyz_path, written


# --- Raw .mrb parsing -------------------------------------------------------
# Automates docs/seeg_slicer_contact_import_plan.md's Steps 1-4, which were
# originally done by hand against data/bella_3dslicer.mrb. Two things in that
# manual pass can't be assumed to generalize and are re-derived per .mrb here
# rather than hardcoded:
#   1. WHICH MarkupsFiducial node holds the per-contact list -- a scene can
#      have more than one (e.g. Bella's had "F", a 20-point per-electrode
#      entry/target set, alongside "Contacts_8", the real 184-point list).
#      Picked as whichever node has the most control points whose labels ALL
#      parse as "electrode name + integer" (e.g. "G1", "K'12") -- Bella's "F"
#      labels ("G'-10") fail this because of the embedded "-", which is
#      exactly the discriminator needed.
#   2. WHICH DIRECTION to apply that node's referenced registration
#      transform. The manual pass found that the transform's exported ITK
#      parameters, applied with the textbook affine formula, did NOT match
#      the direction implied by the transform node's own name/reference (a
#      known BRAINSFit fixed/moving-convention trap) -- confirmed only by
#      checking which direction landed points inside the brainmask. That
#      check is what's automated below: both directions are tried and
#      whichever has the higher in-brain fraction is used. This is a
#      correctness-critical, patient-facing step, so the result is a
#      *preview* (see run_slicer_mrb_parse_job) that must be reviewed and
#      explicitly approved, not written directly.

_CONTACT_LABEL_RE = re.compile(r"^([A-Za-z]+'?)(\d+)$")


def _parse_mrml_references(ref_str):
    """references="display:id1;storage:id2;transform:id3;" -> {"display": "id1", ...}"""
    refs = {}
    for part in (ref_str or "").split(";"):
        part = part.strip()
        if not part or ":" not in part:
            continue
        role, ids = part.split(":", 1)
        ids = ids.strip().split()
        if ids:
            refs[role.strip()] = ids[0]
    return refs


def _find_contact_candidates(mrb_dir, mrml_path):
    """Returns (candidates, nodes_by_id, all_markup_names). Each candidate is
    a dict with name/control_points/coordinate_system/transform_id, for every
    MarkupsFiducial node whose control points ALL match _CONTACT_LABEL_RE."""
    root = ET.parse(mrml_path).getroot()
    nodes_by_id = {}
    markups = []
    for el in root.iter():
        node_id = el.get("id")
        if node_id:
            nodes_by_id[node_id] = el
        if el.tag == "MarkupsFiducial":
            markups.append(el)

    candidates = []
    all_names = []
    for markup in markups:
        name = markup.get("name") or markup.get("id")
        all_names.append(name)
        refs = _parse_mrml_references(markup.get("references"))
        storage_el = nodes_by_id.get(refs.get("storage"))
        if storage_el is None or storage_el.tag != "MarkupsJsonStorage":
            continue  # inline-coordinate (pre-5.x) scenes aren't supported
        file_name = storage_el.get("fileName")
        if not file_name:
            continue
        mrk_path = os.path.join(mrb_dir, unquote(file_name))
        if not os.path.exists(mrk_path):
            continue
        with open(mrk_path) as f:
            data = json.load(f)
        control_points = data["markups"][0]["controlPoints"]
        if not control_points or not all(_CONTACT_LABEL_RE.match(cp.get("label") or "") for cp in control_points):
            continue
        coord_system = data["markups"][0].get("coordinateSystem") or storage_el.get("coordinateSystem") or "LPS"
        candidates.append({
            "name": name,
            "control_points": control_points,
            "coordinate_system": coord_system,
            "transform_id": refs.get("transform"),
        })
    return candidates, nodes_by_id, all_names


def _resolve_transform_h5(nodes_by_id, mrb_dir, transform_id):
    if not transform_id:
        return None
    transform_el = nodes_by_id.get(transform_id)
    if transform_el is None:
        return None
    refs = _parse_mrml_references(transform_el.get("references"))
    storage_el = nodes_by_id.get(refs.get("storage"))
    if storage_el is None:
        return None
    file_name = storage_el.get("fileName")
    if not file_name:
        return None
    path = os.path.join(mrb_dir, unquote(file_name))
    return path if os.path.exists(path) else None


def _load_itk_linear_transform(h5_path):
    with h5py.File(h5_path, "r") as f:
        transform_type = f["TransformGroup/0/TransformType"][()][0]
        if isinstance(transform_type, bytes):
            transform_type = transform_type.decode()
        if not any(k in transform_type for k in ("Affine", "Rigid", "Euler", "Similarity")):
            raise ValueError(
                f"Unsupported registration transform type {transform_type!r} in the .mrb "
                f"(only linear/affine transforms are supported).")
        params = np.asarray(f["TransformGroup/0/TransformParameters"][()], dtype=float)
        fixed = np.asarray(f["TransformGroup/0/TransformFixedParameters"][()], dtype=float)
    return params[:9].reshape(3, 3), params[9:12], fixed[:3]


def _apply_affine(A, t, c, points):
    return (points - c) @ A.T + t + c


def _apply_affine_inverse(A, t, c, points):
    return (points - t - c) @ np.linalg.inv(A).T + c


def _in_brain_fraction(ras_points, mri_dir):
    orig_path = os.path.join(mri_dir, "orig.mgz")
    brainmask_path = os.path.join(mri_dir, "brainmask.mgz")
    orig = nib.load(orig_path)
    brainmask = nib.load(brainmask_path).get_fdata()
    inv_affine = np.linalg.inv(orig.affine)

    homo = np.column_stack([ras_points, np.ones(len(ras_points))])
    voxel_idx = np.round((inv_affine @ homo.T).T[:, :3]).astype(int)
    in_bounds = np.all((voxel_idx >= 0) & (voxel_idx < np.array(orig.shape)), axis=1)
    if not in_bounds.any():
        return 0.0
    in_brain = np.zeros(len(ras_points), dtype=bool)
    b = voxel_idx[in_bounds]
    in_brain[in_bounds] = brainmask[b[:, 0], b[:, 1], b[:, 2]] > 0
    return float(in_brain.mean())


def _surface_ras(ras_points, mri_dir):
    """scanner RAS -> FreeSurfer surface (tkreg) RAS, the space chn-xyz/contacts/
    soz.py expect -- see docs/seeg_slicer_contact_import_plan.md Step 4."""
    orig = nib.load(os.path.join(mri_dir, "orig.mgz"))
    c_ras = np.asarray(orig.header.get("Pxyz_c"))
    return ras_points - c_ras


SLICER_PREVIEW_ARTIFACT_KIND = "slicer_contacts_preview"
LOW_IN_BRAIN_FRACTION_WARNING = 0.3


def parse_mrb(mrb_path, subject: Subject):
    """Parses a 3D Slicer .mrb into a contacts list + diagnostics (see module
    comment above for the two auto-selection heuristics involved). Returns
    (contacts, diagnostics); raises ValueError/FileNotFoundError on anything
    that makes the file unusable -- caller (run_slicer_mrb_parse_job) lets
    that surface as a normal failed job."""
    _, _, mri_dir = _patient_dirs(subject)
    if not os.path.exists(os.path.join(mri_dir, "orig.mgz")) or not os.path.exists(os.path.join(mri_dir, "brainmask.mgz")):
        raise FileNotFoundError(
            "No FreeSurfer reconstruction found for this subject -- run reconstruction first "
            "(needed for both the surface-RAS conversion and the in-brain sanity check).")

    with tempfile.TemporaryDirectory(prefix="mrb_") as tmp:
        with zipfile.ZipFile(mrb_path) as zf:
            zf.extractall(tmp)

        mrml_paths = [
            os.path.join(root, f) for root, _, files in os.walk(tmp) for f in files if f.endswith(".mrml")
        ]
        if not mrml_paths:
            raise ValueError("No .mrml scene file found inside the .mrb")
        mrml_path = mrml_paths[0]
        mrb_dir = os.path.dirname(mrml_path)

        candidates, nodes_by_id, all_names = _find_contact_candidates(mrb_dir, mrml_path)
        if not candidates:
            raise ValueError(
                "No markups node in the .mrb has contact-like labels (electrode name + "
                f"number, e.g. G1, K'12). Markup nodes found: {', '.join(all_names) or '<none>'}.")
        chosen = max(candidates, key=lambda c: len(c["control_points"]))

        raw = np.array([cp["position"] for cp in chosen["control_points"]], dtype=float)
        labels = [cp["label"] for cp in chosen["control_points"]]
        coord_sign = np.array([-1.0, -1.0, 1.0]) if chosen["coordinate_system"].upper() == "LPS" else np.ones(3)

        h5_path = _resolve_transform_h5(nodes_by_id, mrb_dir, chosen["transform_id"])
        if h5_path:
            A, t, c = _load_itk_linear_transform(h5_path)
            fwd_ras = _apply_affine(A, t, c, raw) * coord_sign
            inv_ras = _apply_affine_inverse(A, t, c, raw) * coord_sign
            fwd_frac = _in_brain_fraction(fwd_ras, mri_dir)
            inv_frac = _in_brain_fraction(inv_ras, mri_dir)
            if fwd_frac >= inv_frac:
                ras_points, in_brain_fraction, transform_used = fwd_ras, fwd_frac, "forward"
            else:
                ras_points, in_brain_fraction, transform_used = inv_ras, inv_frac, "inverse"
        else:
            ras_points = raw * coord_sign
            in_brain_fraction = _in_brain_fraction(ras_points, mri_dir)
            transform_used = "none"

        surf_points = _surface_ras(ras_points, mri_dir)

        contacts = []
        for label, xyz in zip(labels, surf_points):
            m = _CONTACT_LABEL_RE.match(label)
            contacts.append({
                "electrode": m.group(1), "contact_index": int(m.group(2)),
                "x": float(xyz[0]), "y": float(xyz[1]), "z": float(xyz[2]),
            })
        _validate_contiguous_indices(contacts)

        warnings = []
        if in_brain_fraction < LOW_IN_BRAIN_FRACTION_WARNING:
            warnings.append(
                f"Only {in_brain_fraction:.0%} of contacts landed inside the brain mask -- the "
                f"chosen node or transform direction may be wrong. Verify carefully before approving.")

        diagnostics = {
            "node_name": chosen["name"],
            "candidate_node_names": [c["name"] for c in candidates],
            "n_points": len(contacts),
            "n_electrodes": len({c["electrode"] for c in contacts}),
            "coordinate_system": chosen["coordinate_system"],
            "transform_used": transform_used,
            "in_brain_fraction": in_brain_fraction,
            "warnings": warnings,
        }
        return contacts, diagnostics


def _clear_slicer_preview(db: Session, subject: Subject):
    old = (
        db.query(Artifact)
        .filter(Artifact.subject_id == subject.id, Artifact.kind == SLICER_PREVIEW_ARTIFACT_KIND)
        .all()
    )
    for a in old:
        path = os.path.join(settings.DATA_ROOT, a.rel_path)
        if os.path.exists(path):
            os.remove(path)
        db.delete(a)
    db.commit()


def run_slicer_mrb_parse_job(db: Session, job: Job, log_file):
    subject = db.query(Subject).filter(Subject.id == job.subject_id).first()
    if not subject:
        raise ValueError("Subject not found")

    params = job.params_json or {}
    mrb_artifact_id = params.get("mrb_artifact_id")
    artifact = (
        db.query(Artifact)
        .filter(Artifact.id == mrb_artifact_id, Artifact.subject_id == subject.id)
        .first()
    )
    if not artifact:
        raise ValueError("mrb artifact not found")
    mrb_path = os.path.join(settings.DATA_ROOT, artifact.rel_path)
    if not os.path.exists(mrb_path):
        raise FileNotFoundError(f"{mrb_path} not found")

    check_cancelled(db, job)
    job.progress_pct = 10.0
    job.progress_message = "Unpacking and parsing .mrb scene"
    db.commit()

    contacts, diagnostics = parse_mrb(mrb_path, subject)

    log_file.write(
        f"Slicer .mrb parsed: node={diagnostics['node_name']!r} "
        f"(candidates: {', '.join(diagnostics['candidate_node_names'])}) "
        f"transform={diagnostics['transform_used']} "
        f"in_brain={diagnostics['in_brain_fraction']:.1%}\n")
    for w in diagnostics["warnings"]:
        log_file.write(f"WARNING: {w}\n")
    log_file.flush()

    _clear_slicer_preview(db, subject)  # only the latest unreviewed preview is ever kept

    _, ct_dir, _ = _patient_dirs(subject)
    os.makedirs(ct_dir, exist_ok=True)
    preview_path = os.path.join(ct_dir, f"{subject.name}_slicer_preview.json")
    with open(preview_path, "w") as f:
        json.dump({"contacts": contacts, "diagnostics": diagnostics}, f)
    register_artifact(db, subject.id, job.id, SLICER_PREVIEW_ARTIFACT_KIND, preview_path)

    job.progress_pct = 95.0
    job.progress_message = (
        f"Preview ready: {diagnostics['n_points']} contacts across {diagnostics['n_electrodes']} "
        f"electrodes from node '{diagnostics['node_name']}' "
        f"({diagnostics['in_brain_fraction']:.0%} in-brain) -- review before approving")
    db.commit()


def load_slicer_preview(db: Session, subject: Subject):
    artifact = (
        db.query(Artifact)
        .filter(Artifact.subject_id == subject.id, Artifact.kind == SLICER_PREVIEW_ARTIFACT_KIND)
        .order_by(Artifact.created_at.desc())
        .first()
    )
    if not artifact:
        raise FileNotFoundError("No pending Slicer import preview for this subject.")
    with open(os.path.join(settings.DATA_ROOT, artifact.rel_path)) as f:
        return json.load(f)


def approve_slicer_preview(db: Session, subject: Subject):
    preview = load_slicer_preview(db, subject)
    chn_xyz_path, written = import_contacts(subject, preview["contacts"])
    register_artifact(db, subject.id, None, "chnXyzDict", chn_xyz_path)
    for path in written:
        register_artifact(db, subject.id, None, "contact_txt", path)
    _clear_slicer_preview(db, subject)
    return {"n_contacts": len(preview["contacts"]), "n_electrodes": len(written)}


def reject_slicer_preview(db: Session, subject: Subject):
    load_slicer_preview(db, subject)  # raises FileNotFoundError if there's nothing to reject
    _clear_slicer_preview(db, subject)


def run_elec_import_job(db: Session, job: Job, log_file):
    subject = db.query(Subject).filter(Subject.id == job.subject_id).first()
    if not subject:
        raise ValueError("Subject not found")

    params = job.params_json or {}
    contacts = params.get("contacts")
    csv_text = params.get("csv_text")
    if not contacts and not csv_text:
        raise ValueError("No contacts or CSV provided")

    check_cancelled(db, job)
    job.progress_pct = 10.0
    job.progress_message = "Parsing contacts CSV" if csv_text else f"Writing {len(contacts)} imported contacts"
    db.commit()

    if csv_text:
        contacts = parse_contacts_csv(csv_text)

    job.progress_pct = 30.0
    job.progress_message = f"Writing {len(contacts)} imported contacts"
    db.commit()

    chn_xyz_path, written = import_contacts(subject, contacts)
    register_artifact(db, subject.id, job.id, "chnXyzDict", chn_xyz_path)
    for path in written:
        register_artifact(db, subject.id, job.id, "contact_txt", path)

    electrode_names = sorted(os.path.splitext(os.path.basename(p))[0] for p in written)
    log_file.write(f"Imported {len(contacts)} contacts across {len(written)} electrodes: "
                    f"{', '.join(electrode_names)}\n")
    log_file.flush()

    job.progress_pct = 95.0
    job.progress_message = f"Imported {len(contacts)} contacts across {len(written)} electrodes"
    db.commit()


def clear_contacts(db: Session, subject: Subject):
    """Deletes clusters (detect()'s labels_npy) and contacts (segment()'s or
    import_contacts()'s chnXyzDict.npy + per-electrode result/freeview .txt
    files) for a subject, plus the matching Artifact rows -- so the electrodes
    tab can be fully reset and redone from scratch, regardless of whether the
    prior contacts came from hough3dlines/GMM/ElectrodeSeg or a Slicer import.
    Also discards any pending (not yet approved/rejected) Slicer import
    preview. Leaves ct_register/detect's CT preprocessing artifacts
    (ct_intra_nii, ct_intracranial_nii) alone -- those aren't clusters/
    contacts, and a re-run of detect() overwrites them anyway."""
    _, ct_dir, _ = _patient_dirs(subject)

    labels_path = os.path.join(ct_dir, f"{subject.name}_labels.npy")
    if os.path.exists(labels_path):
        os.remove(labels_path)

    chn_xyz_path = os.path.join(ct_dir, "chnXyzDict.npy")
    if os.path.exists(chn_xyz_path):
        os.remove(chn_xyz_path)

    for dirname in (f"{subject.name}_result", f"{subject.name}_freeview_result"):
        result_dir = os.path.join(ct_dir, dirname)
        if os.path.exists(result_dir):
            shutil.rmtree(result_dir)

    db.query(Artifact).filter(
        Artifact.subject_id == subject.id,
        Artifact.kind.in_(["labels_npy", "chnXyzDict", "contact_txt"]),
    ).delete(synchronize_session=False)
    db.commit()

    _clear_slicer_preview(db, subject)  # also discard any pending, not-yet-approved Slicer preview


def load_chn_xyz(subject: Subject):
    _, ct_dir, _ = _patient_dirs(subject)
    path = os.path.join(ct_dir, "chnXyzDict.npy")
    if not os.path.exists(path):
        raise FileNotFoundError(f"{path} not found. Run segment() first.")
    elec_dict = np.load(path, allow_pickle=True).item()
    return {label: xyz.tolist() for label, xyz in elec_dict.items()}


def load_contact(subject: Subject, label: str):
    _, ct_dir, _ = _patient_dirs(subject)
    path = os.path.join(ct_dir, f"{subject.name}_result", f"{label}.txt")
    if not os.path.exists(path):
        raise FileNotFoundError(f"No contacts found for electrode label {label!r}.")
    xyz = np.atleast_2d(np.loadtxt(path))
    return xyz.tolist()
