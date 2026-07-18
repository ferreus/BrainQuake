# encoding=utf-8
"""
GUI wrapper around soz_result.py: fuses the ictal module's EI and the
inter-ictal module's HI onto the reconstructed brain surface with SOZ
predictions, wired as a button on the main BrainQuake window instead of
only being reachable from the command line.

This does not recompute EI or HI -- it loads the .npz result files the
Ictal and Inter-ictal module GUIs already save (under <subject_dir>/edf/
EIdets and .../HFOdets) when you click 'ei' / 'HFO detection' there, so
run those first, picking the same subject dir both times. Electrode xyz,
EI result and HI result are all found automatically under the one subject
dir given here -- see soz_result.py's default_elec_xyz_path/find_result_npz.
"""
import os

from PyQt5 import QtWidgets
from PyQt5.QtWidgets import (QFileDialog, QMessageBox, QPushButton, QLineEdit, QLabel,
                              QGridLayout, QTableWidget, QTableWidgetItem, QHeaderView,
                              QDesktopWidget)
from PyQt5.QtGui import QFont

from soz_result import (default_elec_xyz_path, find_result_npz, load_contact_xyz, load_ei_result,
                         load_hi_result, build_result_table, save_csv, plot_3d, resolve_out_prefix)


class SOZResultModule(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super(SOZResultModule, self).__init__(parent)
        self.rows = None
        self.init_gui()

    def init_gui(self):
        self.setWindowTitle('SOZ Result (EI + HI fusion)')
        self.resize(700, 560)
        self.centerWin()

        button_font = QFont()
        button_font.setFamily('Arial')
        button_font.setPointSize(11)

        layout = QGridLayout()

        self.subject_dir_label = QLabel('Subject dir (e.g. data/S1):', self)
        self.subject_dir_edit = QLineEdit(self)
        self.subject_dir_browse = QPushButton('Browse', self)
        layout.addWidget(self.subject_dir_label, 0, 0, 1, 1)
        layout.addWidget(self.subject_dir_edit, 0, 1, 1, 3)
        layout.addWidget(self.subject_dir_browse, 0, 4, 1, 1)

        def browse_subject_dir():
            path = QFileDialog.getExistingDirectory(self, 'Subject dir')
            if path:
                self.subject_dir_edit.setText(path)

        self.subject_dir_browse.clicked.connect(browse_subject_dir)

        self.topn_label = QLabel('Contacts to label in 3D:', self)
        self.topn_edit = QLineEdit('10', self)
        layout.addWidget(self.topn_label, 1, 0, 1, 1)
        layout.addWidget(self.topn_edit, 1, 1, 1, 1)

        self.out_prefix_label = QLabel('Output name (saved inside subject dir):', self)
        self.out_prefix_edit = QLineEdit('soz_result', self)
        layout.addWidget(self.out_prefix_label, 1, 2, 1, 1)
        layout.addWidget(self.out_prefix_edit, 1, 3, 1, 1)

        self.status_label = QLabel("electrode xyz, EI result and HI result are all found automatically "
                                    "under the subject dir -- run the Ictal module ('ei') and Inter-ictal "
                                    "module ('HFO detection') for this subject first.", self)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label, 2, 0, 1, 5)

        self.compute_button = QPushButton('Fuse EI + HI', self)
        self.compute_button.setFont(button_font)
        self.compute_button.setStyleSheet(
            "QPushButton{border-radius:5px;padding:5px;color:#ffffff;background-color:dimgrey;}"
            "QPushButton:hover{background-color:k;}")
        self.compute_button.clicked.connect(self.run_computation)
        layout.addWidget(self.compute_button, 3, 0, 1, 2)

        self.show3d_button = QPushButton('Show 3D result', self)
        self.show3d_button.setFont(button_font)
        self.show3d_button.setStyleSheet(
            "QPushButton{border-radius:5px;padding:5px;color:#ffffff;background-color:dimgrey;}"
            "QPushButton:hover{background-color:k;}")
        self.show3d_button.clicked.connect(self.show_3d)
        self.show3d_button.setEnabled(False)
        layout.addWidget(self.show3d_button, 3, 2, 1, 2)

        self.table = QTableWidget(self)
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(['contact', 'EI', 'HI', 'combined', 'suspect(EI/HI)'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.table, 4, 0, 8, 5)

        self.setLayout(layout)

    def centerWin(self):
        qr = self.frameGeometry()
        desk_center = QDesktopWidget().availableGeometry().center()
        qr.moveCenter(desk_center)
        self.move(qr.topLeft())

    def run_computation(self):
        subject_dir = self.subject_dir_edit.text().strip()
        if not os.path.isdir(subject_dir):
            QMessageBox.warning(self, '', 'Subject dir does not exist.')
            return

        try:
            elec_xyz_path = default_elec_xyz_path(subject_dir)
            if not os.path.isfile(elec_xyz_path):
                raise FileNotFoundError(f'{elec_xyz_path} not found -- run the electrode module first.')
            ei_result_path = find_result_npz(subject_dir, 'EIdets', '_ei.npz')
            hi_result_path = find_result_npz(subject_dir, 'HFOdets', '_events.npz')

            contact_xyz = load_contact_xyz(elec_xyz_path)
            ei_by_chan = load_ei_result(ei_result_path)
            hi_by_chan = load_hi_result(hi_result_path)
        except Exception as e:
            QMessageBox.critical(self, '', f'Failed to load results:\n{e}')
            return

        self.rows = build_result_table(contact_xyz, ei_by_chan, hi_by_chan)
        self.show3d_button.setEnabled(True)
        self.status_label.setText(f'Done. {len(self.rows)} contacts ranked by combined EI/HI score. '
                                   f'(EI: {os.path.basename(ei_result_path)}, HI: {os.path.basename(hi_result_path)})')

        out_prefix = resolve_out_prefix(subject_dir, self.out_prefix_edit.text().strip() or 'soz_result')
        save_csv(self.rows, out_prefix + '.csv')

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
        subject_dir = self.subject_dir_edit.text().strip()
        try:
            top_n = int(self.topn_edit.text())
        except ValueError:
            top_n = 10
        out_prefix = resolve_out_prefix(subject_dir, self.out_prefix_edit.text().strip() or 'soz_result')
        plot_3d(subject_dir, self.rows, top_n, out_prefix + '.png', show=True)
