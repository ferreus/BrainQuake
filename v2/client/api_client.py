"""Thin requests-based HTTP client wrapping every v2 server endpoint (PLAN.md §3).

Replaces utils/surfer_utils.py's pickle-framed socket protocol and every direct call
into utils/elec_utils.py, utils/HI_apis.py, and client_ictal.py's local compute_*
functions -- the GUI modules now POST a job / GET a result instead of running numeric
code (or talking sockets) in-process.
"""
import time
import requests

DEFAULT_BASE_URL = "http://127.0.0.1:8000"

# Job states that mean "the job isn't going to change state on its own anymore"
TERMINAL_JOB_STATES = {"finished", "failed", "cancelled"}


class ApiError(Exception):
    """Raised for any non-2xx response, with the server's `detail` message (if any)
    as the exception text so callers can show it directly in a QMessageBox."""

    def __init__(self, status_code, detail):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"[{status_code}] {detail}")


class ApiClient:
    def __init__(self, base_url=DEFAULT_BASE_URL, timeout=30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    # -- internals ---------------------------------------------------------

    def _url(self, path):
        return f"{self.base_url}{path}"

    def _handle(self, resp):
        if not resp.ok:
            detail = resp.reason
            try:
                body = resp.json()
                detail = body.get("detail", detail)
            except ValueError:
                pass
            raise ApiError(resp.status_code, detail)
        return resp

    def _get(self, path, **kwargs):
        return self._handle(self.session.get(self._url(path), timeout=self.timeout, **kwargs)).json()

    def _post(self, path, json=None, **kwargs):
        return self._handle(self.session.post(self._url(path), json=json, timeout=self.timeout, **kwargs)).json()

    def _put(self, path, json=None, **kwargs):
        return self._handle(self.session.put(self._url(path), json=json, timeout=self.timeout, **kwargs)).json()

    def _download(self, path, dest_path, **kwargs):
        resp = self._handle(self.session.get(self._url(path), timeout=self.timeout, stream=True, **kwargs))
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
        return dest_path

    # -- subjects ------------------------------------------------------------

    def list_subjects(self):
        return self._get("/subjects")

    def create_subject(self, name, hospital=None, recon_type=None):
        return self._post("/subjects", json={"name": name, "hospital": hospital, "recon_type": recon_type})

    def get_subject(self, subject_id):
        return self._get(f"/subjects/{subject_id}")

    def delete_subject(self, subject_id):
        return self._handle(self.session.delete(self._url(f"/subjects/{subject_id}"), timeout=self.timeout)).json()

    def upload_file(self, subject_id, file_type, file_path, filename=None):
        """file_type: 't1' | 'ct' | 'edf' | 'zip'"""
        with open(file_path, "rb") as f:
            files = {"file": (filename or _basename(file_path), f)}
            resp = self.session.post(
                self._url(f"/subjects/{subject_id}/upload"),
                params={"file_type": file_type}, files=files, timeout=self.timeout)
        return self._handle(resp).json()

    def list_artifacts(self, subject_id, kind=None):
        params = {"kind": kind} if kind else None
        return self._get(f"/subjects/{subject_id}/artifacts", params=params)

    def download_subject_zip(self, subject_id, dest_path):
        return self._download(f"/subjects/{subject_id}/download.zip", dest_path)

    # -- jobs ------------------------------------------------------------

    def list_jobs(self, subject_id=None, state=None):
        params = {}
        if subject_id is not None:
            params["subject_id"] = subject_id
        if state is not None:
            params["state"] = state
        return self._get("/jobs", params=params)

    def get_job(self, job_id):
        return self._get(f"/jobs/{job_id}")

    def get_job_log(self, job_id):
        resp = self._handle(self.session.get(self._url(f"/jobs/{job_id}/log"), timeout=self.timeout))
        return resp.text

    def cancel_job(self, job_id):
        return self._post(f"/jobs/{job_id}/cancel")

    def wait_for_job(self, job_id, poll_interval=1.0, timeout=None, on_progress=None):
        """Block until the job reaches a terminal state, calling on_progress(job) after
        every poll if given. Meant to be called from a QThread.run(), not the GUI
        thread -- the Jobs/Logs dock (Phase (e)) is the non-blocking alternative."""
        t0 = time.time()
        while True:
            job = self.get_job(job_id)
            if on_progress:
                on_progress(job)
            if job["state"] in TERMINAL_JOB_STATES:
                return job
            if timeout is not None and (time.time() - t0) > timeout:
                raise TimeoutError(f"Job {job_id} did not finish within {timeout}s (state={job['state']})")
            time.sleep(poll_interval)

    # -- artifacts ------------------------------------------------------------

    def download_artifact(self, artifact_id, dest_path):
        return self._download(f"/artifacts/{artifact_id}/download", dest_path)

    # -- recon ------------------------------------------------------------

    def run_recon(self, subject_id, recon_type="recon-all"):
        return self._post(f"/subjects/{subject_id}/recon", json={"recon_type": recon_type})

    def get_recon_result(self, subject_id):
        return self._get(f"/subjects/{subject_id}/recon/result")

    # -- electrodes ------------------------------------------------------------

    def register_ct(self, subject_id):
        return self._post(f"/subjects/{subject_id}/electrodes/register-ct")

    def detect_electrodes(self, subject_id, K, threshold_pct, erosion_iterations):
        return self._post(f"/subjects/{subject_id}/electrodes/detect", json={
            "K": K, "threshold_pct": threshold_pct, "erosion_iterations": erosion_iterations})

    def update_labels(self, subject_id, exclude_labels=None):
        return self._put(f"/subjects/{subject_id}/electrodes/labels", json={"exclude_labels": exclude_labels})

    def segment_electrodes(self, subject_id, numMax=20, diameterSize=2.5, spacing=2.5, gap=0.0):
        return self._post(f"/subjects/{subject_id}/electrodes/segment", json={
            "numMax": numMax, "diameterSize": diameterSize, "spacing": spacing, "gap": gap})

    def get_chn_xyz(self, subject_id):
        return self._get(f"/subjects/{subject_id}/electrodes/chn-xyz")

    def get_contacts(self, subject_id, label):
        return self._get(f"/subjects/{subject_id}/electrodes/contacts/{label}")

    # -- ictal (EI) ------------------------------------------------------------

    def compute_ei(self, subject_id, edf_artifact_id, baseline_start, baseline_end,
                   target_start, target_end, band_low=1.0, band_high=500.0):
        return self._post(f"/subjects/{subject_id}/ictal/{edf_artifact_id}/ei", json={
            "baseline_start": baseline_start, "baseline_end": baseline_end,
            "target_start": target_start, "target_end": target_end,
            "band_low": band_low, "band_high": band_high})

    def get_ei_result(self, subject_id, edf_artifact_id):
        return self._get(f"/subjects/{subject_id}/ictal/{edf_artifact_id}/ei-result")

    # -- interictal (HFO/HI) ------------------------------------------------------------

    def compute_hfo(self, subject_id, edf_artifact_id, band_low=80.0, band_high=250.0,
                     rel_thresh=2.0, abs_thresh=2.0, min_gap=20.0, min_last=50.0, remain_chns=None):
        return self._post(f"/subjects/{subject_id}/interictal/{edf_artifact_id}/hfo", json={
            "band_low": band_low, "band_high": band_high,
            "rel_thresh": rel_thresh, "abs_thresh": abs_thresh,
            "min_gap": min_gap, "min_last": min_last, "remain_chns": remain_chns})

    def get_hfo_result(self, subject_id, edf_artifact_id):
        return self._get(f"/subjects/{subject_id}/interictal/{edf_artifact_id}/hfo-result")

    # -- soz ------------------------------------------------------------

    def fuse_soz(self, subject_id, ei_artifact_id=None, hi_artifact_id=None):
        return self._post(f"/subjects/{subject_id}/soz/fuse", json={
            "ei_artifact_id": ei_artifact_id, "hi_artifact_id": hi_artifact_id})

    def get_soz_result(self, subject_id):
        return self._get(f"/subjects/{subject_id}/soz/result")


def _basename(path):
    import os
    return os.path.basename(path)
