#! /usr/bin/python3
# -- coding: utf-8 -- **
"""Launcher for the v2 client -- ported from BrainQuake/client_main.py.

Same 5-button layout as the legacy launcher (Phase (e)'s unified QMainWindow with
tabs + a Jobs/Logs dock is a separate, later phase -- this is just Phase (d)'s
REST-wiring of the existing per-module windows). The one structural addition is a
subject picker: the v2 server models each patient as a `Subject` row that has to
exist before any job can be queued against it, whereas the legacy app only ever
dealt with a patient name/folder on disk. The server URL field replaces the old
host/port fields (raw TCP -> REST).
"""
import sys
import json
import os
import logging

from PyQt5.QtWidgets import QApplication, QSizePolicy, QMessageBox, QWidget, \
    QPushButton, QLineEdit, QDesktopWidget, QGridLayout, QLabel, QFrame, QGroupBox, QComboBox
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont

from api_client import ApiClient, ApiError
from client_ictal import IctalModule
from client_inter import InterModule
from client_elec import Electrodes
from client_surf import reconSurferUi
from client_soz import SOZResultModule

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
DEFAULT_BASE_URL = 'http://127.0.0.1:8000'


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {'base_url': DEFAULT_BASE_URL}


def save_config(base_url):
    with open(CONFIG_FILE, 'w') as f:
        json.dump({'base_url': base_url}, f)


class quakeMain(QWidget):
    def __init__(self):
        super(quakeMain, self).__init__()
        self.config = load_config()
        self.api = ApiClient(base_url=self.config.get('base_url', DEFAULT_BASE_URL))
        self.subject = None  # currently selected subject dict ({id, name, ...})
        self.init_gui()
        self.refresh_subjects()

    def init_gui(self):
        self.setWindowTitle('BrainQuake (v2)')
        self.resize(520, 480)
        self.centerWin()
        self.setStyleSheet('background-color:lightgrey;')
        self.setAttribute(Qt.WA_MacShowFocusRect, 0)
        self.gridlayout = QGridLayout()

        self.button_Adaptive = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.button_font = QFont()
        self.button_font.setFamily("Arial")
        self.button_font.setPointSize(18)

        self.mainWords = QLabel(self)
        mainWords_font = QFont()
        mainWords_font.setFamily('black')
        mainWords_font.setPointSize(28)
        mainWords_font.setBold(True)
        self.mainWords.setText('BrainQuake')
        self.mainWords.setFont(mainWords_font)
        self.mainWords.setAlignment(Qt.AlignCenter)
        self.gridlayout.addWidget(self.mainWords, 1, 1, 1, 4)

        # server URL row
        server_font = QFont()
        server_font.setFamily("Arial")
        server_font.setPointSize(11)

        self.server_label = QLabel('Server:', self)
        self.server_label.setFont(server_font)
        self.server_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.gridlayout.addWidget(self.server_label, 2, 1, 1, 1)

        self.url_input = QLineEdit(self)
        self.url_input.setFont(server_font)
        self.url_input.setText(self.config.get('base_url', DEFAULT_BASE_URL))
        self.url_input.editingFinished.connect(self.on_server_changed)
        self.gridlayout.addWidget(self.url_input, 2, 2, 1, 3)

        # subject picker row
        self.subject_label = QLabel('Subject:', self)
        self.subject_label.setFont(server_font)
        self.subject_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.gridlayout.addWidget(self.subject_label, 3, 1, 1, 1)

        self.subject_combo = QComboBox(self)
        self.subject_combo.setFont(server_font)
        self.subject_combo.currentIndexChanged.connect(self.on_subject_selected)
        self.gridlayout.addWidget(self.subject_combo, 3, 2, 1, 2)

        self.subject_refresh_btn = QPushButton('Refresh', self)
        self.subject_refresh_btn.clicked.connect(self.refresh_subjects)
        self.gridlayout.addWidget(self.subject_refresh_btn, 3, 4, 1, 1)

        self.new_subject_name = QLineEdit(self)
        self.new_subject_name.setPlaceholderText('new subject name')
        self.gridlayout.addWidget(self.new_subject_name, 4, 2, 1, 2)

        self.new_subject_btn = QPushButton('Create subject', self)
        self.new_subject_btn.clicked.connect(self.create_subject)
        self.gridlayout.addWidget(self.new_subject_btn, 4, 4, 1, 1)

        self.frame = QGroupBox(self)
        self.frame.setStyleSheet("QGroupBox{border: 2px solid gray; border-radius: 5px;background-color:lightgrey;}QGroupBox:title{subcontrol-origin: margin;subcontrol-position: top left;padding: 0 3px 0 3px;}")
        self.frame.setTitle('Computation Functions')
        self.frame.setFont(self.button_font)

        self.gridlayout.addWidget(self.frame, 5, 1, 4, 4)
        self.frame_layout = QGridLayout()

        self.button_elecs = QPushButton('Electrodes extraction', self)
        self.button_elecs.setStyleSheet("QPushButton{border-radius:5px;padding:5px;color:#ffffff;background-color:dimgrey;}QPushButton:hover{background-color:k}")
        self.button_elecs.setFont(self.button_font)
        self.frame_layout.addWidget(self.button_elecs, 1, 1, 2, 2)
        self.button_elecs.setSizePolicy(self.button_Adaptive)
        self.button_elecs.clicked.connect(self.elecs_computation)

        self.button_surfs = QPushButton('Surface reconstruction', self)
        self.button_surfs.setStyleSheet("QPushButton{border-radius:5px;padding:5px;color:#ffffff;background-color:dimgrey;}QPushButton:hover{background-color:k}")
        self.button_surfs.setFont(self.button_font)
        self.frame_layout.addWidget(self.button_surfs, 1, 3, 2, 2)
        self.button_surfs.setSizePolicy(self.button_Adaptive)
        self.button_surfs.clicked.connect(self.surfs_computation)

        self.button_ictal = QPushButton('Ictal module', self)
        self.button_ictal.setStyleSheet("QPushButton{border-radius:5px;padding:5px;color:#ffffff;background-color:dimgrey;}QPushButton:hover{background-color:k}")
        self.button_ictal.setFont(self.button_font)
        self.frame_layout.addWidget(self.button_ictal, 3, 1, 2, 2)
        self.button_ictal.setSizePolicy(self.button_Adaptive)
        self.button_ictal.clicked.connect(self.ictal_computation)

        self.button_inter = QPushButton('Interictal module', self)
        self.button_inter.setStyleSheet("QPushButton{border-radius:5px;padding:5px;color:#ffffff;background-color:dimgrey;}QPushButton:hover{background-color:k}")
        self.button_inter.setFont(self.button_font)
        self.frame_layout.addWidget(self.button_inter, 3, 3, 2, 2)
        self.button_inter.setSizePolicy(self.button_Adaptive)
        self.button_inter.clicked.connect(self.inter_computation)

        self.button_soz = QPushButton('SOZ Result', self)
        self.button_soz.setStyleSheet("QPushButton{border-radius:5px;padding:5px;color:#ffffff;background-color:dimgrey;}QPushButton:hover{background-color:k}")
        self.button_soz.setFont(self.button_font)
        self.frame_layout.addWidget(self.button_soz, 5, 1, 2, 4)
        self.button_soz.setSizePolicy(self.button_Adaptive)
        self.button_soz.clicked.connect(self.soz_computation)

        self.frame.setLayout(self.frame_layout)
        self.setLayout(self.gridlayout)
        self.show()

    def centerWin(self):
        qr = self.frameGeometry()
        DeskCenter = QDesktopWidget().availableGeometry().center()
        qr.moveCenter(DeskCenter)
        self.move(qr.topLeft())

    def on_server_changed(self):
        base_url = self.url_input.text().strip() or DEFAULT_BASE_URL
        self.api = ApiClient(base_url=base_url)
        self.config['base_url'] = base_url
        save_config(base_url)
        self.refresh_subjects()

    def refresh_subjects(self):
        try:
            subjects = self.api.list_subjects()
        except (ApiError, Exception) as e:
            logger.warning(f"Could not reach server at {self.api.base_url}: {e}")
            return
        self.subject_combo.blockSignals(True)
        self.subject_combo.clear()
        for s in subjects:
            self.subject_combo.addItem(f"{s['name']} (#{s['id']})", s)
        self.subject_combo.blockSignals(False)
        if subjects:
            self.subject_combo.setCurrentIndex(0)
            self.subject = subjects[0]

    def on_subject_selected(self, index):
        self.subject = self.subject_combo.itemData(index)

    def create_subject(self):
        name = self.new_subject_name.text().strip()
        if not name:
            QMessageBox.warning(self, '', 'Enter a subject name first.')
            return
        try:
            subject = self.api.create_subject(name)
        except ApiError as e:
            QMessageBox.critical(self, '', f'Failed to create subject:\n{e}')
            return
        self.new_subject_name.clear()
        self.refresh_subjects()
        idx = self.subject_combo.findText(f"{subject['name']} (#{subject['id']})")
        if idx >= 0:
            self.subject_combo.setCurrentIndex(idx)

    def _require_subject(self):
        if not self.subject:
            QMessageBox.warning(self, '', 'Select or create a subject first.')
            return False
        return True

    def ictal_computation(self):
        if not self._require_subject():
            return
        self.ictal_widget = IctalModule(self.api, self.subject)
        self.ictal_widget.show()

    def inter_computation(self):
        if not self._require_subject():
            return
        self.inter_widget = InterModule(self.api, self.subject)
        self.inter_widget.show()

    def elecs_computation(self):
        if not self._require_subject():
            return
        self.elec_widget = Electrodes(self.api, self.subject)
        self.elec_widget.show()

    def surfs_computation(self):
        self.surf_widget = reconSurferUi(self.api)
        self.surf_widget.show()

    def soz_computation(self):
        if not self._require_subject():
            return
        self.soz_widget = SOZResultModule(self.api, self.subject)
        self.soz_widget.show()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    win = quakeMain()
    sys.exit(app.exec_())
