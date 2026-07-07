#! /usr/bin/python3.6
# -- coding: utf-8 -- **

import sys
import json
import os

from PyQt5.QtWidgets import QApplication,  QMainWindow, QSizePolicy, QMessageBox, QWidget, \
    QPushButton, QLineEdit, QDesktopWidget, QGridLayout, QFileDialog,  QListWidget, QLabel,QFrame,QGroupBox
from PyQt5.QtCore import Qt, QThread
from PyQt5.QtGui import QFont,QPixmap

from client_ictal import IctalModule
from client_inter import InterModule
from client_elec import Electrodes
from client_surf import reconSurferUi

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
DEFAULT_HOST = '127.0.0.1'
DEFAULT_PORT = 6669


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {'host': DEFAULT_HOST, 'port': DEFAULT_PORT}


def save_config(host, port):
    with open(CONFIG_FILE, 'w') as f:
        json.dump({'host': host, 'port': port}, f)


class quakeMain(QWidget):
    def __init__(self):
        super(quakeMain,self).__init__()
        self.config = load_config()
        self.init_gui()

    def init_gui(self):
        self.setWindowTitle('BrainQuake')
        self.resize(500,340)
        self.centerWin()
        self.setStyleSheet('background-color:lightgrey;')
        self.setAttribute(Qt.WA_MacShowFocusRect,0)
        self.gridlayout=QGridLayout()

        #pre setting
        self.button_Adaptive = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.button_font=QFont()
        self.button_font.setFamily("Arial")
        self.button_font.setPointSize(18)

        self.mainLabel=QLabel(self)
        self.mainLabel.setPixmap(QPixmap('../docs/round_icon_min.png'))
        self.mainLabel.setAlignment(Qt.AlignCenter)
        self.gridlayout.addWidget(self.mainLabel,1,2,1,1)

        self.mainWords=QLabel(self)
        self.mainWords_font=QFont()
        self.mainWords_font.setFamily('black')
        self.mainWords_font.setPointSize(35)
        self.mainWords_font.setBold(True)
        self.mainWords.setText('BrainQuake')
        self.mainWords.setFont(self.mainWords_font)
        self.gridlayout.addWidget(self.mainWords,1,3,1,1)

        # server settings row
        server_font = QFont()
        server_font.setFamily("Arial")
        server_font.setPointSize(11)

        self.server_label = QLabel('Server:', self)
        self.server_label.setFont(server_font)
        self.server_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.gridlayout.addWidget(self.server_label, 2, 1, 1, 1)

        self.host_input = QLineEdit(self)
        self.host_input.setFont(server_font)
        self.host_input.setText(self.config.get('host', DEFAULT_HOST))
        self.host_input.setPlaceholderText('IP address')
        self.host_input.editingFinished.connect(self.on_server_changed)
        self.gridlayout.addWidget(self.host_input, 2, 2, 1, 1)

        self.colon_label = QLabel(':', self)
        self.colon_label.setFont(server_font)
        self.colon_label.setAlignment(Qt.AlignCenter)
        self.gridlayout.addWidget(self.colon_label, 2, 3, 1, 1)

        self.port_input = QLineEdit(self)
        self.port_input.setFont(server_font)
        self.port_input.setText(str(self.config.get('port', DEFAULT_PORT)))
        self.port_input.setPlaceholderText('port')
        self.port_input.setMaximumWidth(80)
        self.port_input.editingFinished.connect(self.on_server_changed)
        self.gridlayout.addWidget(self.port_input, 2, 4, 1, 1)

        self.frame=QGroupBox(self)
        self.frame.setStyleSheet("QGroupBox{border: 2px solid gray; border-radius: 5px;background-color:lightgrey;}QGroupBox:title{subcontrol-origin: margin;subcontrol-position: top left;padding: 0 3px 0 3px;}")
        self.frame.setTitle('Computation Functions')
        self.frame.setFont(self.button_font)

        self.gridlayout.addWidget(self.frame,3,1,4,4)
        self.frame_layout=QGridLayout()


        # electrodes extraction
        self.button_elecs = QPushButton('Electrodes extraction', self)
        self.button_elecs.setToolTip('extract seeg electrodes locations')
        self.button_elecs.setStyleSheet("QPushButton{border-radius:5px;padding:5px;color:#ffffff;background-color:dimgrey;}QPushButton:hover{background-color:k}")
        self.button_elecs.setFont(self.button_font)
        self.frame_layout.addWidget(self.button_elecs, 1, 1, 2, 2)
        self.button_elecs.setSizePolicy(self.button_Adaptive)
        self.button_elecs.clicked.connect(self.elecs_computation)

        # surface reconstruction
        self.button_surfs = QPushButton('Surface reconstruction', self)
        self.button_surfs.setToolTip('pial surface reconstruction')
        self.button_surfs.setStyleSheet("QPushButton{border-radius:5px;padding:5px;color:#ffffff;background-color:dimgrey;}QPushButton:hover{background-color:k}")
        self.button_surfs.setFont(self.button_font)
        self.frame_layout.addWidget(self.button_surfs, 1, 3, 2, 2)
        self.button_surfs.setSizePolicy(self.button_Adaptive)
        self.button_surfs.clicked.connect(self.surfs_computation)

        # ictal module
        self.button_ictal=QPushButton('Ictal module', self)
        self.button_ictal.setToolTip('compute epilepsy index(EI) & full band characteristic(Full Band)')
        self.button_ictal.setStyleSheet("QPushButton{border-radius:5px;padding:5px;color:#ffffff;background-color:dimgrey;}QPushButton:hover{background-color:k}")
        self.button_ictal.setFont(self.button_font)
        self.frame_layout.addWidget(self.button_ictal,3,1,2,2)
        self.button_ictal.setSizePolicy(self.button_Adaptive)
        self.button_ictal.clicked.connect(self.ictal_computation)

        # interictal module
        self.button_inter = QPushButton('Interictal module', self)
        self.button_inter.setToolTip('compute high frequency events index(HI)')
        self.button_inter.setStyleSheet("QPushButton{border-radius:5px;padding:5px;color:#ffffff;background-color:dimgrey;}QPushButton:hover{background-color:k}")
        self.button_inter.setFont(self.button_font)
        self.frame_layout.addWidget(self.button_inter, 3, 3, 2, 2)
        self.button_inter.setSizePolicy(self.button_Adaptive)
        self.button_inter.clicked.connect(self.inter_computation)


        self.frame.setLayout(self.frame_layout)
        self.setLayout(self.gridlayout)
        self.show()

    def centerWin(self):
        qr=self.frameGeometry()
        DeskCenter=QDesktopWidget().availableGeometry().center()
        qr.moveCenter(DeskCenter)
        self.move(qr.topLeft())

    def on_server_changed(self):
        host = self.host_input.text().strip()
        try:
            port = int(self.port_input.text().strip())
        except ValueError:
            self.port_input.setText(str(self.config.get('port', DEFAULT_PORT)))
            return
        self.config['host'] = host
        self.config['port'] = port
        save_config(host, port)

    def _current_host(self):
        return self.host_input.text().strip() or DEFAULT_HOST

    def _current_port(self):
        try:
            return int(self.port_input.text().strip())
        except ValueError:
            return DEFAULT_PORT

    def ictal_computation(self):
        self.ictal_widget=IctalModule(self)
        self.ictal_widget.show()

    def inter_computation(self):
        self.inter_widget=InterModule(self)
        self.inter_widget.show()

    def elecs_computation(self):
        self.elec_widget=Electrodes()
        self.elec_widget.show()

    def surfs_computation(self):
        self.surf_widget=reconSurferUi(host=self._current_host(), port=self._current_port())
        self.surf_widget.show()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    win = quakeMain()
    sys.exit(app.exec_())
