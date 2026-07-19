# encoding=utf-8
"""SOZ tab -- ported from BrainQuake/client_soz.py.

soz_result.py's fusion/ranking (load_contact_xyz/load_ei_result/load_hi_result/
build_result_table) now runs server-side (v2/server/app/services/soz.py) behind
`POST .../soz/fuse` + `GET .../soz/result` -- this module just triggers that job and
renders the returned rows. The mayavi 3D render (plot_3d) is unchanged code, just
fed by the REST rows instead of a locally-built table, and reading lh.pial/rh.pial
out of the same downloaded-and-unzipped recon dir every other tab uses
(local_store.ensure_recon_unzipped) instead of a user-picked subject_dir.
"""
import csv
import os

from PyQt5 import QtWidgets
from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import (QMessageBox, QPushButton, QLineEdit, QLabel,
                              QGridLayout, QTableWidget, QTableWidgetItem, QHeaderView,
                              QDesktopWidget)
from PyQt5.QtGui import QFont

import nibabel as nib
import numpy as np

from api_client import ApiError
import local_store


def save_csv(rows, out_csv):
    with open(out_csv, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def plot_3d(recon_dir, rows, top_n, out_png, show):
    from mayavi import mlab

    verl, facel = nib.freesurfer.read_geometry(os.path.join(recon_dir, 'surf', 'lh.pial'))
    verr, facer = nib.freesurfer.read_geometry(os.path.join(recon_dir, 'surf', 'rh.pial'))
    all_ver = np.concatenate([verl, verr], axis=0)
    all_face = np.concatenate([facel, facer + verl.shape[0]], axis=0)

    xs = np.array([r['x'] for r in rows])
    ys = np.array([r['y'] for r in rows])
    zs = np.array([r['z'] for r in rows])
    scores = np.array([r['combined_score'] for r in rows])

    mlab.figure(bgcolor=(0.9, 0.9, 0.9), size=(1200, 1200))
    mesh = mlab.triangular_mesh(all_ver[:, 0], all_ver[:, 1], all_ver[:, 2], all_face,
                                 color=(1., 1., 1.), opacity=0.35, line_width=1.)
    mesh.actor.property.backface_culling = True

    pts = mlab.points3d(xs, ys, zs, scores, scale_mode='none', scale_factor=2.5,
                        colormap='plasma', vmin=0.0, vmax=1.0)
    mlab.colorbar(pts, title='SOZ suspicion score', orientation='vertical')

    for r in rows[:top_n]:
        mlab.text3d(r['x'] + 3, r['y'] + 3, r['z'] + 3, r['contact'], scale=1.8, color=(0, 0, 1))

    if out_png:
        mlab.savefig(out_png)
    if show:
        mlab.show()


class SozFuseThread(QThread):
    done = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, api, subject_id):
        super(SozFuseThread, self).__init__()
        self.api = api
        self.subject_id = subject_id

    def run(self):
        try:
            job = self.api.fuse_soz(self.subject_id)
            final = self.api.wait_for_job(job['id'], poll_interval=1.0)
            if final['state'] != 'finished':
                self.failed.emit(final.get('progress_message') or f"job {final['state']}")
                return
            rows = self.api.get_soz_result(self.subject_id)
            self.done.emit(rows)
        except (ApiError, Exception) as e:
            self.failed.emit(str(e))


class SOZResultModule(QtWidgets.QWidget):
    def __init__(self, api, subject, parent=None):
        super(SOZResultModule, self).__init__(parent)
        self.api = api
        self.subject = subject
        self.rows = None
        self.fuse_thread = None
        self.init_gui()

    def init_gui(self):
        self.setWindowTitle(f"SOZ Result (EI + HI fusion) -- {self.subject['name']}")
        self.resize(700, 560)
        self.centerWin()

        button_font = QFont()
        button_font.setFamily('Arial')
        button_font.setPointSize(11)

        layout = QGridLayout()

        self.topn_label = QLabel('Contacts to label in 3D:', self)
        self.topn_edit = QLineEdit('10', self)
        layout.addWidget(self.topn_label, 0, 0, 1, 1)
        layout.addWidget(self.topn_edit, 0, 1, 1, 1)

        self.status_label = QLabel(
            "Fuses the subject's most recent EI (ictal) and HI (inter-ictal) results -- "
            "run those tabs first.", self)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label, 1, 0, 1, 4)

        self.compute_button = QPushButton('Fuse EI + HI', self)
        self.compute_button.setFont(button_font)
        self.compute_button.setStyleSheet(
            "QPushButton{border-radius:5px;padding:5px;color:#ffffff;background-color:dimgrey;}"
            "QPushButton:hover{background-color:k;}")
        self.compute_button.clicked.connect(self.run_computation)
        layout.addWidget(self.compute_button, 2, 0, 1, 2)

        self.show3d_button = QPushButton('Show 3D result', self)
        self.show3d_button.setFont(button_font)
        self.show3d_button.setStyleSheet(
            "QPushButton{border-radius:5px;padding:5px;color:#ffffff;background-color:dimgrey;}"
            "QPushButton:hover{background-color:k;}")
        self.show3d_button.clicked.connect(self.show_3d)
        self.show3d_button.setEnabled(False)
        layout.addWidget(self.show3d_button, 2, 2, 1, 2)

        self.table = QTableWidget(self)
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(['contact', 'EI', 'HI', 'combined', 'suspect(EI/HI)'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.table, 3, 0, 8, 5)

        self.setLayout(layout)

    def centerWin(self):
        qr = self.frameGeometry()
        desk_center = QDesktopWidget().availableGeometry().center()
        qr.moveCenter(desk_center)
        self.move(qr.topLeft())

    def run_computation(self):
        self.compute_button.setEnabled(False)
        self.fuse_thread = SozFuseThread(self.api, self.subject['id'])
        self.fuse_thread.done.connect(self._fuse_done)
        self.fuse_thread.failed.connect(self._fuse_failed)
        self.fuse_thread.start()

    def _fuse_failed(self, msg):
        self.compute_button.setEnabled(True)
        QMessageBox.critical(self, '', f'Failed to fuse EI/HI:\n{msg}')

    def _fuse_done(self, rows):
        self.compute_button.setEnabled(True)
        self.rows = rows
        self.show3d_button.setEnabled(True)
        self.status_label.setText(f'Done. {len(self.rows)} contacts ranked by combined EI/HI score.')

        out_csv = os.path.join(local_store.subject_dir(self.subject['name']), 'soz_result.csv')
        save_csv(self.rows, out_csv)

        self.table.setRowCount(0)
        for r in self.rows:
            row_idx = self.table.rowCount()
            self.table.insertRow(row_idx)
            self.table.setItem(row_idx, 0, QTableWidgetItem(r['contact']))
            self.table.setItem(row_idx, 1, QTableWidgetItem(f"{r['ei']:.3f}"))
            self.table.setItem(row_idx, 2, QTableWidgetItem(f"{r['hi']:.0f}"))
            self.table.setItem(row_idx, 3, QTableWidgetItem(f"{r['combined_score']:.3f}"))
            self.table.setItem(row_idx, 4, QTableWidgetItem(f"{r['suspect_ei']}/{r['suspect_hi']}"))

    def show_3d(self):
        if not self.rows:
            return
        try:
            top_n = int(self.topn_edit.text())
        except ValueError:
            top_n = 10
        try:
            recon_dir = local_store.ensure_recon_unzipped(self.api, self.subject['id'], self.subject['name'])
        except (ApiError, Exception) as e:
            QMessageBox.critical(self, '', f'Could not fetch reconstruction for 3D view:\n{e}')
            return
        out_png = os.path.join(local_store.subject_dir(self.subject['name']), 'soz_result.png')
        plot_3d(recon_dir, self.rows, top_n, out_png, show=True)
