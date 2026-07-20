#! /usr/bin/python3
# -- coding: utf-8 -- **
"""Unified Qt UI (Phase (e), PHASE_E_PLAN.md) -- replaces client_main.py's launcher +
5 independent windows with a single QMainWindow: a tab per pipeline stage, a Patients
side dock, a Jobs/Logs bottom dock, and a status-bar connection indicator.

Import order matters here: mayavi_view must be imported before any tab module that
itself imports mayavi (client_surf, client_elec, client_soz all do a module-level
`from mayavi import mlab`) -- ETSConfig binds to whichever Qt toolkit is active at
mayavi's first import and can't be changed afterwards. Don't reorder these imports.
"""
import sys
import os
import json
import logging

import mayavi_view  # noqa: F401  -- import first, see module docstring
from mayavi_view import MayaviView

from PyQt5 import QtWidgets, QtGui
from PyQt5.QtCore import Qt

from api_client import ApiClient
from app_state import AppState
from connection_monitor import ConnectionMonitor
from patients_panel import PatientsPanel
from jobs_panel import JobsPanel

from client_surf import reconSurferUi
from client_elec import Electrodes
from client_ictal import IctalModule
from client_inter import InterModule
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


class ServerSettingsDialog(QtWidgets.QDialog):
    def __init__(self, current_url, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Server settings')
        layout = QtWidgets.QFormLayout(self)
        self.url_edit = QtWidgets.QLineEdit(current_url, self)
        layout.addRow('Server URL:', self.url_edit)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def url(self):
        return self.url_edit.text().strip() or DEFAULT_BASE_URL


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = load_config()
        base_url = self.config.get('base_url', DEFAULT_BASE_URL)
        self.app_state = AppState(ApiClient(base_url=base_url))

        self.setWindowTitle('BrainQuake (v2)')
        self.resize(1600, 1000)

        self._build_tabs()
        self._build_docks()
        self._build_menu()
        self._build_status_bar()

        self.app_state.subjectChanged.connect(self._on_subject_changed)

        self.connection_monitor = ConnectionMonitor(get_base_url=lambda: self.app_state.api.base_url)
        self.connection_monitor.statusChanged.connect(self._on_connection_status)
        self.connection_monitor.start()

    # -- construction -----------------------------------------------------------

    def _build_tabs(self):
        api = self.app_state.api

        self.recon_tab = reconSurferUi(api)
        self.elec_tab = Electrodes(api, subject=None)
        self.ictal_tab = IctalModule(api, subject=None)
        self.inter_tab = InterModule(api, subject=None)
        self.soz_tab = SOZResultModule(api, subject=None)

        recon_page = self._wrap_with_mayavi(self.recon_tab)
        elec_page = self._wrap_with_mayavi(self.elec_tab)

        self.tabs = QtWidgets.QTabWidget(self)
        self.tabs.addTab(recon_page, 'MRI/CT Surface Reconstruction')
        self.tabs.addTab(elec_page, 'Electrodes Extraction')
        self.tabs.addTab(self.ictal_tab, 'Ictal module')
        self.tabs.addTab(self.inter_tab, 'Interictal module')
        self.tabs.addTab(self.soz_tab, 'Visualization / Results')
        self.setCentralWidget(self.tabs)

    def _wrap_with_mayavi(self, tab_widget, bgcolor=(0.8, 0.8, 0.8)):
        """recon/electrodes tabs come from hand-built gui_forms layouts that already
        occupy their whole widget -- rather than editing those generated forms, embed
        their mayavi view alongside them in a splitter at this level, and hand the
        tab widget a reference to draw into (see client_surf.py's attach_mayavi_view /
        client_elec.py's attach_mayavi_view)."""
        view = MayaviView(bgcolor=bgcolor)
        tab_widget.attach_mayavi_view(view)
        splitter = QtWidgets.QSplitter(Qt.Horizontal)
        splitter.addWidget(tab_widget)
        splitter.addWidget(view)
        splitter.setSizes([700, 700])
        return splitter

    def _build_docks(self):
        self.patients_panel = PatientsPanel(self.app_state, self)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.patients_panel)

        self.jobs_panel = JobsPanel(self.app_state, self)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.jobs_panel)

    def _build_menu(self):
        file_menu = self.menuBar().addMenu('&File')
        server_action = file_menu.addAction('Server settings...')
        server_action.triggered.connect(self._open_server_settings)
        file_menu.addSeparator()
        quit_action = file_menu.addAction('Quit')
        quit_action.triggered.connect(self.close)

        view_menu = self.menuBar().addMenu('&View')
        view_menu.addAction(self.patients_panel.toggleViewAction())
        view_menu.addAction(self.jobs_panel.toggleViewAction())

    def _build_status_bar(self):
        self.connection_label = QtWidgets.QLabel('Connecting...')
        self.connection_label.setStyleSheet('padding: 2px 8px;')
        self.statusBar().addPermanentWidget(self.connection_label)

    # -- server settings -----------------------------------------------------------

    def _open_server_settings(self):
        dlg = ServerSettingsDialog(self.app_state.api.base_url, self)
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        base_url = dlg.url()
        new_api = ApiClient(base_url=base_url)
        self.app_state.set_api(new_api)
        self.config['base_url'] = base_url
        save_config(base_url)

        # propagate the new client to every tab's `.api` attribute -- each tab reads
        # `self.api` at call time, so this is enough for any *new* action; threads
        # already created at tab-construction time (e.g. Electrodes' JobPollThreads)
        # keep the api reference they were built with until re-created, which only
        # matters if a job is started mid-flight while switching servers (edge case,
        # not otherwise guarded against here)
        for tab in (self.recon_tab, self.elec_tab, self.ictal_tab, self.inter_tab, self.soz_tab):
            tab.api = new_api

    # -- subject propagation -----------------------------------------------------------

    def _on_subject_changed(self, subject):
        for tab in (self.recon_tab, self.elec_tab, self.ictal_tab, self.inter_tab, self.soz_tab):
            tab.set_subject(subject)

    # -- connection status -----------------------------------------------------------

    def _on_connection_status(self, ok, detail):
        color = '#2e7d32' if ok else '#c62828'
        self.connection_label.setStyleSheet(f'padding: 2px 8px; color: white; background-color: {color};')
        self.connection_label.setText(detail)

    # -- shutdown -----------------------------------------------------------

    def _active_threads(self):
        candidates = [
            getattr(self.recon_tab, 'thread_1', None),
            getattr(self.recon_tab, 'thread_3', None),
            getattr(self.elec_tab, 'thread_register', None),
            getattr(self.elec_tab, 'thread_detect', None),
            getattr(self.elec_tab, 'thread_segment', None),
            getattr(self.ictal_tab, 'ei_thread', None),
            getattr(self.inter_tab, 'hi_thread', None),
            getattr(self.soz_tab, 'fuse_thread', None),
        ]
        return [t for t in candidates if t is not None and t.isRunning()]

    def closeEvent(self, event):
        active = self._active_threads()
        if active:
            reply = QtWidgets.QMessageBox.question(
                self, 'Operations in progress',
                f'{len(active)} operation(s) are still in progress (uploads/recon/compute). '
                'Quitting now will not stop server-side jobs, but any client-side upload/poll '
                'in flight will be abandoned. Quit anyway?',
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
            if reply != QtWidgets.QMessageBox.Yes:
                event.ignore()
                return

        self.connection_monitor.stop()
        self.patients_panel.shutdown()
        self.jobs_panel.shutdown()
        event.accept()


if __name__ == '__main__':
    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.show()
    app.exec_()
    # Embedding mayavi scenes via TraitsUI's SceneEditor (mayavi_view.py) segfaults
    # during normal Python/VTK teardown on process exit -- confirmed by manual testing:
    # a bare pop-out mlab.show() window does NOT crash on exit on this same machine,
    # so the crash is specific to the embedded-widget pattern (likely a VTK render
    # window vs. Qt widget destruction-order issue in this VTK/PyQt5 build). All user
    # data is already persisted server-side by the time the window closes, so a hard
    # process exit here is safe -- this is not "losing" anything, it just skips
    # Python's normal (here, crash-prone) object finalization.
    os._exit(0)
