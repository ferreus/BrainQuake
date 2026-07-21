"""New Patient dialog -- replaces the old "MRI/CT Surface Reconstruction" tab
(client_surf.py / gui_forms/surfer_form.py, both retired) entirely, per explicit user
redesign decision. Standard Qt theme: no custom stylesheets, no groupboxes, no
"check existing tasks" table (redundant with the Jobs panel) -- just the fields
actually needed to kick off an upload + reconstruction:

  1. Reconstruction type: label + combobox
  2. MRI: line edit (read-only path display) + Browse button
  3. CT: line edit (read-only path display) + Browse button (optional)
  4. Upload / Cancel buttons

No patient-name field -- the name is derived from the MRI filename (same convention
the legacy recon tab used: everything before the first 'T', e.g. "S1T1.nii.gz" ->
"S1"). No hospital field -- removed from the app entirely per user decision.

Clicking Upload closes the dialog immediately (open_new_patient_dialog() below hands
off to a background UploadThread parented to the Jobs panel, so it survives after this
dialog is gone) -- all progress and any failure surfaces in the Jobs panel's pending-row
support (jobs_panel.py), never here, since there's nothing left open to show a
QMessageBox to once this dialog has closed.
"""
import os
import threading
import time
import logging

from PyQt5 import QtWidgets
from PyQt5.QtCore import QThread, pyqtSignal

from api_client import ApiError, UploadCancelled

logger = logging.getLogger(__name__)

RECON_TYPES = ['recon-all', 'fast-surfer', 'infant-surfer']


def derive_patient_name(mri_path):
    """Same convention the legacy recon tab used: patient name is the MRI filename up
    to the first 'T' (e.g. "S1T1.nii.gz" -> "S1"). Fragile but kept as-is -- the name
    really is "derived from the MRI file", per explicit user decision, not something
    this redesign should silently change."""
    basename = os.path.basename(mri_path)
    return basename.split('T')[0]


class NewPatientDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.mri_path = None
        self.ct_path = None
        self.recon_type = RECON_TYPES[0]
        self.setWindowTitle('New Patient')
        self._init_ui()

    def _init_ui(self):
        # deliberately no setStyleSheet/QGroupBox anywhere -- standard Qt theme only
        layout = QtWidgets.QFormLayout(self)

        self.recon_combo = QtWidgets.QComboBox(self)
        self.recon_combo.addItems(RECON_TYPES)
        layout.addRow('Reconstruction type:', self.recon_combo)

        mri_row = QtWidgets.QHBoxLayout()
        self.mri_edit = QtWidgets.QLineEdit(self)
        self.mri_edit.setReadOnly(True)
        mri_browse = QtWidgets.QPushButton('Browse...', self)
        mri_browse.clicked.connect(self._browse_mri)
        mri_row.addWidget(self.mri_edit)
        mri_row.addWidget(mri_browse)
        layout.addRow('MRI (T1):', mri_row)

        ct_row = QtWidgets.QHBoxLayout()
        self.ct_edit = QtWidgets.QLineEdit(self)
        self.ct_edit.setReadOnly(True)
        ct_browse = QtWidgets.QPushButton('Browse...', self)
        ct_browse.clicked.connect(self._browse_ct)
        ct_row.addWidget(self.ct_edit)
        ct_row.addWidget(ct_browse)
        layout.addRow('CT (optional):', ct_row)

        buttons = QtWidgets.QDialogButtonBox(self)
        buttons.addButton('Upload', QtWidgets.QDialogButtonBox.AcceptRole)
        buttons.addButton('Cancel', QtWidgets.QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self._on_upload_clicked)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def _browse_mri(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, 'Select MRI (T1) file', '', 'Nifti Files (*.nii.gz);;All Files (*)')
        if path:
            self.mri_path = path
            self.mri_edit.setText(path)

    def _browse_ct(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, 'Select CT file', '', 'Nifti Files (*.nii.gz);;All Files (*)')
        if path:
            self.ct_path = path
            self.ct_edit.setText(path)

    def _on_upload_clicked(self):
        if not self.mri_path:
            QtWidgets.QMessageBox.warning(self, '', 'Select an MRI (T1) file first.')
            return
        self.recon_type = self.recon_combo.currentText()
        self.accept()


class UploadThread(QThread):
    """Creates/reuses the subject, uploads MRI (+CT) with real byte-level progress,
    then queues the recon job. Runs entirely after the dialog has already closed --
    every signal here is consumed by jobs_panel.py's pending-row support, not by any
    UI owned by this thread itself."""
    progress = pyqtSignal(float, str)     # 0-100, status message
    handed_off = pyqtSignal(dict)         # the created recon job, once queued
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    # Minimum time between progress signal emissions -- requests-toolbelt's
    # MultipartEncoderMonitor callback fires on every chunk read (can be tens to
    # hundreds of times per second for a fast local upload), and each one used to
    # trigger a Jobs panel row update; throttling here cuts that emission rate
    # dramatically at the source, on top of jobs_panel.py's in-place progress-bar
    # update (the other half of the flicker fix).
    _EMIT_INTERVAL = 0.1

    def __init__(self, api, mri_path, ct_path, recon_type, parent=None):
        super().__init__(parent)
        self.api = api
        self.mri_path = mri_path
        self.ct_path = ct_path
        self.recon_type = recon_type
        self.cancel_event = threading.Event()
        self._last_emit_t = 0.0

    def _emit_throttled(self, pct, msg, is_last=False):
        now = time.monotonic()
        if not is_last and (now - self._last_emit_t) < self._EMIT_INTERVAL:
            return
        self._last_emit_t = now
        self.progress.emit(pct, msg)

    def run(self):
        try:
            patient_name = derive_patient_name(self.mri_path)
            existing = [s for s in self.api.list_subjects() if s['name'] == patient_name]
            subject = existing[0] if existing else self.api.create_subject(patient_name)
            subject_id = subject['id']

            # 0-55% for MRI if a CT follows, else 0-90%; recon job progress itself is
            # tracked separately by the Jobs panel's normal polling once queued below
            mri_upper = 55.0 if self.ct_path else 90.0

            def mri_progress(sent, total):
                pct = (sent / total) * mri_upper
                self._emit_throttled(pct, f'Uploading MRI... {sent // 1024}KB / {total // 1024}KB',
                                      is_last=(sent == total))

            self.api.upload_file_with_progress(
                subject_id, 't1', self.mri_path,
                on_progress=mri_progress, cancel_event=self.cancel_event)

            if self.ct_path:
                def ct_progress(sent, total):
                    pct = 55.0 + (sent / total) * 35.0
                    self._emit_throttled(pct, f'Uploading CT... {sent // 1024}KB / {total // 1024}KB',
                                          is_last=(sent == total))

                self.api.upload_file_with_progress(
                    subject_id, 'ct', self.ct_path,
                    on_progress=ct_progress, cancel_event=self.cancel_event)

            if self.cancel_event.is_set():
                raise UploadCancelled('upload cancelled')

            self.progress.emit(95.0, 'Starting reconstruction job...')
            job = self.api.run_recon(subject_id, recon_type=self.recon_type)
            self.handed_off.emit(job)
        except UploadCancelled:
            self.cancelled.emit()
        except (ApiError, Exception) as e:
            self.failed.emit(str(e))


def open_new_patient_dialog(api, jobs_panel, parent=None):
    """Shows the dialog; if accepted, hands the upload off to a background thread
    parented to `jobs_panel` (so it stays alive for as long as the app runs, well
    past this dialog closing) and registers a pending row for it."""
    dlg = NewPatientDialog(parent=parent)
    if dlg.exec_() != QtWidgets.QDialog.Accepted:
        return

    thread = UploadThread(api, dlg.mri_path, dlg.ct_path, dlg.recon_type, parent=jobs_panel)
    pending_id = jobs_panel.add_pending(
        subject_id=None, subject_name=derive_patient_name(dlg.mri_path),
        label='upload', cancel_event=thread.cancel_event)

    def _on_progress(pct, msg):
        jobs_panel.update_pending(pending_id, progress_pct=pct, message=msg, state='uploading')

    def _on_handed_off(job):
        jobs_panel.remove_pending(pending_id)
        jobs_panel.poll_thread.refresh_now()

    def _on_failed(msg):
        logger.warning(f"New Patient upload failed: {msg}")
        jobs_panel.update_pending(pending_id, message=msg, state='failed')

    def _on_cancelled():
        jobs_panel.update_pending(pending_id, message='Upload cancelled by user.', state='cancelled')

    thread.progress.connect(_on_progress)
    thread.handed_off.connect(_on_handed_off)
    thread.failed.connect(_on_failed)
    thread.cancelled.connect(_on_cancelled)
    thread.start()
