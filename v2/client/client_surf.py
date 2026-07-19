#! /usr/bin/python3
# -- coding: utf-8 -- **
"""Recon tab -- ported from BrainQuake/client_surf.py.

The legacy version spoke a raw pickle-framed socket protocol (utils/surfer_utils.py)
to upload a zipped T1(+CT), poll a flat-file task queue, and download the finished
recon zip. All of that becomes REST: POST .../upload (per file, not zipped), POST
.../recon (queues a job row), GET /jobs/{id} polling, GET .../download.zip. The
mayavi pial-mesh preview is unchanged -- it already worked by unzipping a downloaded
recon archive and reading surf/lh.pial locally, which is exactly what
local_store.ensure_recon_unzipped does now.
"""
import sys
import os
import logging

import nibabel as nib
import numpy as np
from mayavi import mlab

import PyQt5
from PyQt5 import QtWidgets, QtCore, QtGui
from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import QFileDialog, QTableWidgetItem

from api_client import ApiError
import local_store
from gui_forms.surfer_form import Ui_reconSurfer

logger = logging.getLogger(__name__)


class UploadAndReconThread(QThread):
    progressBarValue = pyqtSignal(int)
    log = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, api):
        super(UploadAndReconThread, self).__init__()
        self.api = api

    def run(self):
        try:
            # create-or-reuse the subject this T1 belongs to
            existing = [s for s in self.api.list_subjects() if s['name'] == self.patientName]
            subject = existing[0] if existing else self.api.create_subject(self.patientName)
            self.subject_id = subject['id']

            self.progressBarValue.emit(5)
            self.api.upload_file(self.subject_id, 't1', self.t1Filepath)
            self.progressBarValue.emit(20)
            if self.ctFilepath:
                self.api.upload_file(self.subject_id, 'ct', self.ctFilepath)
            self.progressBarValue.emit(30)

            job = self.api.run_recon(self.subject_id, recon_type=self.reconType)
            self.job_id = job['id']

            def on_progress(j):
                # recon progress is 0-100 server-side already; map it onto the
                # 30-100 range we've got left after upload
                pct = 30 + int(0.7 * j.get('progress_pct', 0))
                self.progressBarValue.emit(min(pct, 100))
                self.log.emit(j.get('progress_message') or '')

            final_job = self.api.wait_for_job(self.job_id, poll_interval=5.0, on_progress=on_progress)
            if final_job['state'] != 'finished':
                self.failed.emit(final_job.get('progress_message') or f"job {final_job['state']}")
                return
            self.progressBarValue.emit(100)
            self.log.emit('Reconstruction finished.')
        except (ApiError, Exception) as e:
            self.failed.emit(str(e))


class DownloadThread(QThread):
    downloadValue = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, api):
        super(DownloadThread, self).__init__()
        self.api = api

    def run(self):
        try:
            dest_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'download')
            os.makedirs(dest_dir, exist_ok=True)
            dest_path = os.path.join(dest_dir, f"{self.subjectName}.zip")
            self.api.download_subject_zip(self.subjectId, dest_path)
            self.downloadValue.emit('Done!')
        except (ApiError, Exception) as e:
            self.failed.emit(str(e))


class reconSurferUi(QtWidgets.QWidget, Ui_reconSurfer):
    def __init__(self, api):
        super(reconSurferUi, self).__init__()
        self.setupUi(self)
        self.api = api
        self.Filename_ct = None
        self.ctFilepath = None
        self.subjects_by_row = []
        self.thread_1 = UploadAndReconThread(api)
        self.thread_1.progressBarValue.connect(self.progressValue)
        self.thread_1.log.connect(self.progressLog)
        self.thread_1.failed.connect(self.uploadFailed)
        self.thread_3 = DownloadThread(api)
        self.thread_3.downloadValue.connect(self.downloadProgress)
        self.thread_3.failed.connect(self.downloadFailed)
        self.refreshSubjects()

    def browseT1File(self):
        self.directory = QFileDialog.getOpenFileName(self, \
             "getOpenFileName", "", "All Files (*);;Nifti Files (*.nii.gz)")
        self.Filepath_t1 = self.directory[0]
        self.Filename_t1 = self.directory[0].split('/')[-1]
        self.Patname = self.directory[0].split('/')[-1].split('T')[0]
        self.textBrowser.setText(self.Filepath_t1)
        self.textBrowser_2.setText(self.Patname)
        self.progressBar.setValue(0)

    def browseCTFile(self):
        self.directory = QFileDialog.getOpenFileName(self, \
             "getOpenFileName", "", "All Files (*);;Nifti Files (*.nii.gz)")
        self.Filepath_ct = self.directory[0]
        self.Filename_ct = self.directory[0].split('/')[-1]
        self.textBrowser_1.setText(self.Filepath_ct)
        self.ctFilepath = self.Filepath_ct

    def uploadT1File(self):
        self.reconType = ['recon-all', 'fast-surfer', 'infant-surfer'][self.comboBox.currentIndex()]
        self.thread_1.patientName = self.Patname
        self.thread_1.t1Filepath = self.Filepath_t1
        self.thread_1.ctFilepath = self.ctFilepath
        self.thread_1.reconType = self.reconType
        self.thread_1.start()

    def uploadFailed(self, msg):
        QtWidgets.QMessageBox.critical(self, '', f'Reconstruction failed:\n{msg}')

    def checkProgress(self):
        self.refreshSubjects()

    def refreshSubjects(self):
        try:
            subjects = self.api.list_subjects()
        except (ApiError, Exception) as e:
            logger.warning(f"Could not list subjects: {e}")
            return
        self.subjects_by_row = subjects
        self.tableWidget.clearContents()
        self.tableWidget.setRowCount(0)
        for s in subjects:
            try:
                jobs = self.api.list_jobs(subject_id=s['id'])
            except (ApiError, Exception):
                jobs = []
            recon_jobs = [j for j in jobs if j['job_type'] == 'recon']
            state = recon_jobs[-1]['state'] if recon_jobs else 'no recon yet'
            row = self.tableWidget.rowCount()
            self.tableWidget.setRowCount(row + 1)
            self.tableWidget.setItem(row, 0, QTableWidgetItem(f"{s['id']} {s['name']} {state}"))

    def downloadRecon(self):
        self.itemsSelected()
        if not self.items:
            return
        row = self.indexes[0]
        subject = self.subjects_by_row[row]
        self.thread_3.subjectId = subject['id']
        self.thread_3.subjectName = subject['name']
        self.thread_3.start()

    def downloadFailed(self, msg):
        QtWidgets.QMessageBox.critical(self, '', f'Download failed:\n{msg}')

    def previewRecon(self):
        self.itemsSelected()
        if not self.items:
            return
        row = self.indexes[0]
        subject = self.subjects_by_row[row]
        self.mayaviplot(subject)

    def mayaviplot(self, subject):
        try:
            recon_dir = local_store.ensure_recon_unzipped(self.api, subject['id'], subject['name'])
        except (ApiError, Exception) as e:
            QtWidgets.QMessageBox.critical(self, '', f'Could not fetch reconstruction:\n{e}')
            return
        lh_pial_file = os.path.join(recon_dir, 'surf', 'lh.pial')
        rh_pial_file = os.path.join(recon_dir, 'surf', 'rh.pial')
        if not (os.path.exists(lh_pial_file) and os.path.exists(rh_pial_file)):
            QtWidgets.QMessageBox.warning(self, '', 'No finished reconstruction found for this subject.')
            return
        verl, facel = nib.freesurfer.read_geometry(lh_pial_file)
        verr, facer = nib.freesurfer.read_geometry(rh_pial_file)
        verall = np.concatenate([verl, verr], axis=0)
        facer = facer + verl.shape[0]
        faceall = np.concatenate([facel, facer], axis=0)
        mlab.triangular_mesh(verall[:, 0], verall[:, 1], verall[:, 2], faceall)
        mlab.draw()
        mlab.show()

    def itemsSelected(self):
        items = self.tableWidget.selectedItems()
        indexes = self.tableWidget.selectedIndexes()
        self.items = []
        self.indexes = []
        for item in items:
            item = item.text()
            self.items.append(item)
        for index in indexes:
            index = index.row()
            self.indexes.append(index)

    def progressValue(self, i):
        self.progressBar.setValue(i)

    def progressLog(self, log_read):
        self.textBrowser_2.setText(log_read)

    def downloadProgress(self, info):
        if self.indexes:
            row = self.indexes[0]
            self.tableWidget.setItem(row, 1, QTableWidgetItem(info))


if __name__ == "__main__":
    from api_client import ApiClient
    app = QtWidgets.QApplication(sys.argv)
    widget = reconSurferUi(ApiClient())
    widget.setFixedSize(860, 640)
    widget.show()
    sys.exit(app.exec_())
