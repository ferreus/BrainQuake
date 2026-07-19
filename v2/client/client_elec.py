#! /usr/bin/python3
# -- coding: utf-8 -- **
"""Electrodes tab -- ported from BrainQuake/client_elec.py.

The legacy version ran hough3dlines + GMM clustering + ElectrodeSeg locally via
utils/elec_utils.py's QThreads (Preprocess_thread, GenerateLabel_thread,
ContactSegment_thread). All of that numeric work now lives server-side
(v2/server/app/services/electrodes.py); this module POSTs a detect/segment job and
polls it instead. Two workflow changes fall out of that:

  - The legacy app split "Preprocess" (btn3, threshold+erode) and "Label Gen" (btn5,
    hough3dlines+GMM) into two separate steps/threads. The v2 `detect` job does both
    in one shot (services/electrodes.py's detect() combines preprocess_ct() +
    generate_labels()), so both buttons here trigger the same detect() call -- btn5
    is effectively "redo detect with the current parameters".
  - `OptimizeParams_thread`'s grid-search threshold/erosion tuner was not ported to
    v2/server (out of scope for Phase (b)/(d) per PLAN.md's router checklist), so
    "Optimize" (btn11) just explains that and does nothing else. Set threshold/K/
    erosion by hand for now.

All matplotlib 3D scatter / mayavi rendering code is unchanged from the legacy
version -- only its data source changed, from locally-computed arrays to
REST-downloaded artifacts (via local_store / api_client).
"""
import sys
import os
import re
import logging

import nibabel as nib
import numpy as np
from mayavi import mlab
import matplotlib
matplotlib.use("Qt5Agg")
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from mpl_toolkits.mplot3d import Axes3D

import PyQt5
from PyQt5 import QtWidgets, QtCore, QtGui
from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import QFileDialog, QGraphicsScene, QTableWidgetItem

from gui_forms.elec_form import Electrodes_gui
from api_client import ApiError
import local_store
from anat_lookup import lookupTable

logger = logging.getLogger(__name__)


class JobPollThread(QThread):
    """Generic "start a job, poll until terminal" thread -- replaces the legacy
    per-stage QThreads (Preprocess_thread/GenerateLabel_thread/ContactSegment_thread),
    which each ran their numeric work in-process. Now the numeric work runs
    server-side; this thread just starts the job and polls app.services.electrodes
    via api_client.wait_for_job."""
    progress = pyqtSignal(str)
    finished_ok = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, api, start_fn):
        super(JobPollThread, self).__init__()
        self.api = api
        self.start_fn = start_fn  # callable() -> job dict (already queued)

    def run(self):
        try:
            job = self.start_fn()

            def on_progress(j):
                self.progress.emit(j.get('progress_message') or '')

            final_job = self.api.wait_for_job(job['id'], poll_interval=2.0, on_progress=on_progress)
            if final_job['state'] != 'finished':
                self.failed.emit(final_job.get('progress_message') or f"job {final_job['state']}")
                return
            self.finished_ok.emit(final_job)
        except (ApiError, Exception) as e:
            self.failed.emit(str(e))


class Electrodes(QtWidgets.QWidget, Electrodes_gui):
    def __init__(self, api, subject):
        super(Electrodes, self).__init__()
        self.setupUi(self)
        self.api = api
        self.subject = subject
        self.patient = subject['name']
        self.scene = QGraphicsScene()
        self.graphicsView.setScene(self.scene)
        self.fig = Figure(figsize=(10, 10))
        self.axes = self.fig.add_subplot(111, projection='3d')
        self.c = ['#698E6A', '#896D47', '#D4BF89', '#106898', '#954024', '#A35F65',
                  '#535164', '#CDD171', '#BED2BB', '#4C1E1A', '#F5B087', '#CC5D20',
                  '#003460', '#ED6D46', '#822327', '#1E2732', '#6B4C9A', '#2E8B6B',
                  '#C9A227', '#B24592']
        self.labels = None
        self.K = None
        self.chn_xyz = None

        self.thread_register = JobPollThread(self.api, self._start_ct_register)
        self.thread_register.finished_ok.connect(self.ctRegisterFinished)
        self.thread_register.failed.connect(self.jobFailed)
        self.thread_detect = JobPollThread(self.api, self._start_detect)
        self.thread_detect.finished_ok.connect(self.detectFinished)
        self.thread_detect.failed.connect(self.jobFailed)
        self.thread_segment = JobPollThread(self.api, self._start_segment)
        self.thread_segment.finished_ok.connect(self.segmentFinished)
        self.thread_segment.failed.connect(self.jobFailed)

        logger.info(f"Electrodes GUI initialized for subject={self.patient}")
        self.lineEdit_1.setText(self.patient)
        self.lineEdit_1.setReadOnly(True)
        self.lineEdit_2.setText(subject.get('hospital') or '')

    def jobFailed(self, msg):
        QtWidgets.QMessageBox.critical(self, '', msg)

    def patientName(self):
        self.patient = self.lineEdit_1.text()

    def hospitalName(self):
        pass

    def numberK(self):
        self.pushButton_3.setEnabled(True)
        self.pushButton_11.setEnabled(True)

    def numberEro(self):
        pass

    def threSel(self):
        pass

    # -- surf/CT import (btn2/btn1) -----------------------------------------

    def importSurf(self):
        # Legacy picked a local FreeSurfer subject folder here; in v2 the subject
        # is already selected on the main window, so this just confirms it and
        # moves on to CT import (auto-skipping straight past it if CT
        # registration was already run for this subject in an earlier session).
        self.pushButton_1.setEnabled(True)
        try:
            reg_artifacts = self.api.list_artifacts(self.subject['id'], kind='ct_reg_nii')
        except (ApiError, Exception) as e:
            logger.warning(f"could not check existing CT registration: {e}")
            reg_artifacts = []
        if reg_artifacts:
            self._enable_detect_controls()

    def importCT(self):
        path, _ = QFileDialog.getOpenFileName(self, "getOpenFileName", "", "All Files (*);;Nifti Files (*.nii.gz)")
        if not path:
            return
        self.pushButton_1.setEnabled(False)
        self._ct_local_path = path
        self.thread_register.start()

    def _start_ct_register(self):
        self.api.upload_file(self.subject['id'], 'ct', self._ct_local_path)
        return self.api.register_ct(self.subject['id'])

    def ctRegisterFinished(self, job):
        logger.info("CT registration finished")
        self._enable_detect_controls()

    def _enable_detect_controls(self):
        self.lineEdit_3.setEnabled(True)
        self.lineEdit_4.setEnabled(True)
        self.doubleSpinBox_1.setEnabled(True)
        self.pushButton_3.setEnabled(True)
        self.pushButton_11.setEnabled(True)
        if not self.lineEdit_4.text():
            self.lineEdit_4.setText('10')
        if not self.doubleSpinBox_1.value():
            self.doubleSpinBox_1.setValue(10)

    def optimizeParams(self):
        QtWidgets.QMessageBox.information(
            self, '',
            "The threshold/erosion auto-tuner (OptimizeParams_thread) wasn't ported "
            "to the v2 server -- set K / threshold / erosion by hand below.")

    # -- detect (btn3 Preprocess / btn5 Label Gen -- both trigger the combined job) --

    def preprocessData(self):
        try:
            self.K = int(self.lineEdit_3.text())
            self.ero_itr = int(self.lineEdit_4.text())
            self.thre = float(self.doubleSpinBox_1.value())
        except ValueError:
            QtWidgets.QMessageBox.warning(self, '', 'Enter numeric K / erosion / threshold values.')
            return
        self.pushButton_3.setEnabled(False)
        self.pushButton_5.setEnabled(False)
        logger.info(f"preprocessData: starting detect job K={self.K} thre={self.thre} ero={self.ero_itr}")
        self.thread_detect.start()

    def labelGen(self):
        self.preprocessData()

    def _start_detect(self):
        return self.api.detect_electrodes(self.subject['id'], K=self.K, threshold_pct=self.thre,
                                           erosion_iterations=self.ero_itr)

    def detectFinished(self, job):
        logger.info(f"detectFinished: {job.get('progress_message')}")
        self.pushButton_3.setEnabled(True)
        self.pushButton_5.setEnabled(True)
        self.pushButton_4.setEnabled(True)
        self._load_labels_and_plot()
        self.pushButton_8.setEnabled(True)

    def viewIntra(self):
        # Legacy's PreprocessResult_thread scattered the thresholded intracranial
        # point cloud before clustering; here we download that same volume (the
        # detect job's ct_intracranial_nii artifact) and extract it locally.
        try:
            artifacts = self.api.list_artifacts(self.subject['id'], kind='ct_intracranial_nii')
            if not artifacts:
                QtWidgets.QMessageBox.warning(self, '', 'Run detect() first.')
                return
            dest = os.path.join(local_store.subject_dir(self.patient), 'ct_intracranial_preview.nii.gz')
            self.api.download_artifact(artifacts[-1]['id'], dest)
        except (ApiError, Exception) as e:
            QtWidgets.QMessageBox.critical(self, '', f'Could not fetch preview volume:\n{e}')
            return
        data = nib.load(dest).get_fdata()
        xs, ys, zs = np.where(data != 0)

        self.fig = Figure(figsize=(10, 10))
        self.scene.addWidget(FigureCanvas(self.fig))
        self.axes = self.fig.add_subplot(111, projection='3d')
        self.axes.set_title(f"Preprocessed Electrodes, patient={self.patient}")
        self.axes.set_xlim(0, 256)
        self.axes.set_ylim(0, 256)
        self.axes.set_zlim(0, 256)
        self.axes.set_axis_off()
        self.axes.scatter(xs, ys, zs, marker='.', c='blue')
        self.graphicsView.show()

    # -- labels review (btn6 view, btn8 confirm) -----------------------------------------

    def _load_labels_and_plot(self):
        try:
            artifacts = self.api.list_artifacts(self.subject['id'], kind='labels_npy')
            if not artifacts:
                return
            dest = os.path.join(local_store.subject_dir(self.patient), 'labels_preview.npy')
            self.api.download_artifact(artifacts[-1]['id'], dest)
        except (ApiError, Exception) as e:
            logger.warning(f"could not fetch labels: {e}")
            return
        self.labels = np.load(dest, allow_pickle=True)
        self.K = len(np.unique(self.labels)) - 1
        self.pushButton_6.setEnabled(True)

        self.fig = Figure(figsize=(10, 10))
        self.scene.addWidget(FigureCanvas(self.fig))
        self.axes.clear()
        self.axes = self.fig.add_subplot(111, projection='3d')
        self.axes.set_title(f"Clustered {self.K} Electrodes, patient={self.patient}")
        self.axes.set_xlim(0, 256)
        self.axes.set_ylim(0, 256)
        self.axes.set_zlim(0, 256)
        self.axes.set_axis_off()
        for i in range(self.K):
            indx, indy, indz = np.where(self.labels == i + 1)
            self.axes.scatter(indx, indy, indz, marker='.', c=self.c[i % len(self.c)])
        self.graphicsView.show()

    def viewLabels(self):
        self._load_labels_and_plot()

    def labelsDone(self):
        # "commit reviewed labels" -- confirm as-is (no exclusions). A future pass
        # could wire a per-cluster exclude-checkbox list to
        # api.update_labels(exclude_labels=[...]) for the "drop a noisy track"
        # workflow PUT .../labels was built for (see services/electrodes.py's
        # commit_labels).
        try:
            self.api.update_labels(self.subject['id'], exclude_labels=None)
        except (ApiError, Exception) as e:
            QtWidgets.QMessageBox.critical(self, '', f'Could not confirm labels:\n{e}')
            return
        self.pushButton_1.setEnabled(False)
        self.pushButton_3.setEnabled(False)
        self.pushButton_5.setEnabled(False)
        self.pushButton_6.setEnabled(False)
        self.pushButton_7.setEnabled(True)

    # -- segment (btn7) -----------------------------------------

    def contactSeg(self):
        self.pushButton_7.setEnabled(False)
        logger.info(f"contactSeg: starting segment job for {self.K} electrodes")
        self.thread_segment.start()

    def _start_segment(self):
        return self.api.segment_electrodes(self.subject['id'], numMax=20, diameterSize=2.5, spacing=2.5, gap=0)

    def segmentFinished(self, job):
        logger.info("segmentFinished: segment job finished")
        self.pushButton_9.setEnabled(True)

    def elecAdjust(self):
        pass

    # -- contacts review (btn9) -----------------------------------------

    def viewContacts(self):
        try:
            self.chn_xyz = {k: np.array(v) for k, v in self.api.get_chn_xyz(self.subject['id']).items()}
        except (ApiError, Exception) as e:
            QtWidgets.QMessageBox.critical(self, '', f'Could not fetch contact coordinates:\n{e}')
            return

        try:
            recon_dir = local_store.ensure_recon_unzipped(self.api, self.subject['id'], self.patient)
        except (ApiError, Exception) as e:
            logger.warning(f"could not fetch recon dir for anatomical lookup: {e}")
            recon_dir = None

        self.elec_label_dict = {}
        self.tableWidget.clearContents()
        self.tableWidget.setRowCount(0)
        for item, xyz in sorted(self.chn_xyz.items()):
            row = self.tableWidget.rowCount()
            self.tableWidget.setRowCount(row + 1)
            self.tableWidget.setItem(row, 0, QTableWidgetItem(item))
            self.tableWidget.setItem(row, 1, QTableWidgetItem(str(xyz.shape[0])))
            if recon_dir:
                try:
                    labels_name = lookupTable(recon_dir, xyz)
                    self.tableWidget.setItem(row, 2, QTableWidgetItem(labels_name[0]))
                    self.elec_label_dict[item] = labels_name
                except Exception as e:
                    logger.warning(f"anatomical lookup failed for {item}: {e}")
        logger.info(f"viewContacts: anatomical labels resolved: {self.elec_label_dict}")

        self.fig = Figure(figsize=(10, 10))
        self.scene.addWidget(FigureCanvas(self.fig))
        self.axes.clear()
        self.axes = self.fig.add_subplot(111, projection='3d')
        self.axes.set_title(f"Segmented contacts of {self.K} Electrodes, patient={self.patient}")
        self.axes.set_xlim(0, 256)
        self.axes.set_ylim(0, 256)
        self.axes.set_zlim(0, 256)
        self.axes.set_axis_off()
        if self.labels is not None:
            for i in range(self.K):
                indx, indy, indz = np.where(self.labels == i + 1)
                self.axes.scatter(indx, indy, indz, marker='.', c=self.c[i % len(self.c)])
        for item, xyz in self.chn_xyz.items():
            self.axes.scatter(128 - xyz[:, 0], 128 - xyz[:, 2], 128 + xyz[:, 1], marker='*', c='red')
            self.axes.text(130 - xyz[0, 0], 130 - xyz[0, 2], 130 + xyz[0, 1], f"{item}", c='black')
        self.graphicsView.show()

        self.pushButton_10.setEnabled(True)

    # -- final 3D view (btn10) -----------------------------------------

    def allSet(self):
        self.vis3D()

    def vis3D(self):
        try:
            recon_dir = local_store.ensure_recon_unzipped(self.api, self.subject['id'], self.patient)
            chn_xyz = self.chn_xyz or {k: np.array(v) for k, v in self.api.get_chn_xyz(self.subject['id']).items()}
        except (ApiError, Exception) as e:
            QtWidgets.QMessageBox.critical(self, '', f'Could not fetch data for 3D view:\n{e}')
            return

        verl, facel = nib.freesurfer.read_geometry(os.path.join(recon_dir, 'surf', 'lh.pial'))
        verr, facer = nib.freesurfer.read_geometry(os.path.join(recon_dir, 'surf', 'rh.pial'))
        all_ver = np.concatenate([verl, verr], axis=0)
        tmp_facer = facer + verl.shape[0]
        all_face = np.concatenate([facel, tmp_facer], axis=0)

        opacity = 0.4
        mlab.figure(bgcolor=(0.8, 0.8, 0.8), size=(1500, 1500))
        mesh = mlab.triangular_mesh(all_ver[:, 0], all_ver[:, 1], all_ver[:, 2], all_face,
                                     color=(1., 1., 1.), representation='surface', opacity=opacity, line_width=1.)
        mesh.actor.property.ambient = 0.4225
        mesh.actor.property.specular = 0.3
        mesh.actor.property.specular_power = 20
        mesh.actor.property.diffuse = 0.5
        mesh.actor.property.interpolation = 'phong'
        mesh.actor.property.backface_culling = True
        if opacity <= 1.0:
            mesh.scene.renderer.trait_set(use_depth_peeling=True)
        for child in mlab.get_engine().scenes[0].children:
            poly_data_normals = child.children[0]
            poly_data_normals.filter.feature_angle = 80.0

        for chnn, xyz in chn_xyz.items():
            for j in range(xyz.shape[0]):
                mlab.points3d(xyz[j, 0], xyz[j, 1], xyz[j, 2], color=(0, 0, 0), scale_factor=1.5)
            mlab.text3d(xyz[-1, 0] + 4, xyz[-1, 1] + 4, xyz[-1, 2] + 4, chnn, orient_to_camera=True,
                        color=(0, 0, 1), line_width=10, scale=2)

        logger.info("vis3D: 3D scene drawn")
        mlab.draw()
        mlab.show()


if __name__ == "__main__":
    from api_client import ApiClient
    logger.info("Starting Electrodes module standalone")
    app = QtWidgets.QApplication(sys.argv)
    api = ApiClient()
    subjects = api.list_subjects()
    if not subjects:
        print("No subjects on the server -- create one first (e.g. via client_main.py).")
        sys.exit(1)
    widget = Electrodes(api, subjects[0])
    widget.showMaximized()
    widget.show()
    sys.exit(app.exec_())
