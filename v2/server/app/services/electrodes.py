import os
import re
import sys
import math
import time
import subprocess
import numpy as np
import nibabel as nib
from scipy.ndimage import binary_erosion
from sklearn.mixture import GaussianMixture as GMM
from sklearn.linear_model import LinearRegression, Lasso
from sqlalchemy.orm import Session
from app.config import settings
from app.models import Job, Subject, Artifact
from app.services.recon import register_artifact
from app.services.job_control import check_cancelled, run_and_track_subprocess

# Ported from BrainQuake/utils/elec_utils.py. Split into two job types per PLAN.md
# 2.7: detect() (Preprocess_thread + GenerateLabel_thread -- hough3dlines + GMM
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
    with open(hough_file, 'r') as f:
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
    matplotlib scatter showed."""
    _, ct_dir, _ = _patient_dirs(subject)
    labels_path = os.path.join(ct_dir, f"{subject.name}_labels.npy")
    if not os.path.exists(labels_path):
        raise FileNotFoundError(f"{labels_path} not found. Run detect() first.")

    Labels = np.load(labels_path)
    values = sorted(v for v in np.unique(Labels) if v != 0)
    clusters = []
    for v in values:
        idx = np.where(Labels == v)
        clusters.append({
            "label": int(v),
            "voxel_count": int(len(idx[0])),
            "centroid": [float(np.mean(idx[0])), float(np.mean(idx[1])), float(np.mean(idx[2]))],
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
