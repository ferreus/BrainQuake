"""Patients (subjects) side dock (PHASE_E_PLAN.md §2).

Replaces client_main.py's subject combo box with a persistent, always-visible list that
every tab reacts to via AppState.subjectChanged, plus a delete action. Background
refresh runs on its own QThread (SubjectPollThread) -- never a GUI-thread QTimer calling
`requests` directly -- so a slow/unreachable server can't freeze the dock (or the rest
of the window) while it waits on a response.

There is no inline "create subject by name" field here -- subjects are only ever
created via the "New Patient..." flow (new_patient_dialog.py), since the patient name
is always derived from the uploaded MRI file, never typed in by hand. This panel just
emits newPatientRequested when its button is clicked; main_window.py owns actually
opening the dialog (it needs both the api client and the Jobs panel, and keeping this
panel decoupled from jobs_panel.py mirrors how every other cross-panel interaction goes
through AppState/signals instead of direct references).
"""
import threading
import logging

from PyQt5 import QtWidgets, QtCore
from PyQt5.QtCore import QThread, pyqtSignal, Qt

from api_client import ApiError

logger = logging.getLogger(__name__)

POLL_INTERVAL = 10.0


class SubjectPollThread(QThread):
    subjectsUpdated = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, get_api, interval=POLL_INTERVAL, parent=None):
        super().__init__(parent)
        self._get_api = get_api
        self.interval = interval
        self._stop = False
        self._wake = threading.Event()

    def stop(self):
        self._stop = True
        self._wake.set()
        self.wait(int(self.interval * 1000) + 1000)

    def refresh_now(self):
        self._wake.set()

    def run(self):
        while not self._stop:
            try:
                subjects = self._get_api().list_subjects()
                self.subjectsUpdated.emit(subjects)
            except (ApiError, Exception) as e:
                self.failed.emit(str(e))
            self._wake.wait(self.interval)
            self._wake.clear()


class PatientsPanel(QtWidgets.QDockWidget):
    newPatientRequested = pyqtSignal()

    def __init__(self, app_state, parent=None):
        super().__init__('Patients', parent)
        self.app_state = app_state
        self.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.setFeatures(QtWidgets.QDockWidget.DockWidgetMovable | QtWidgets.QDockWidget.DockWidgetFloatable)

        self._subjects_by_row = []

        self.poll_thread = SubjectPollThread(get_api=lambda: self.app_state.api)
        self.poll_thread.subjectsUpdated.connect(self._on_subjects_updated)
        self.poll_thread.failed.connect(self._on_refresh_failed)

        self._init_ui()

        self.poll_thread.start()
        self.app_state.apiChanged.connect(lambda _api: self.poll_thread.refresh_now())

    def _init_ui(self):
        container = QtWidgets.QWidget(self)
        layout = QtWidgets.QVBoxLayout(container)

        self.filter_edit = QtWidgets.QLineEdit(self)
        self.filter_edit.setPlaceholderText('Filter by name...')
        self.filter_edit.textChanged.connect(self._apply_filter)
        layout.addWidget(self.filter_edit)

        self.error_banner = QtWidgets.QLabel(self)
        self.error_banner.setStyleSheet('color: white; background-color: #b00020; padding: 4px; border-radius: 3px;')
        self.error_banner.setWordWrap(True)
        self.error_banner.hide()
        layout.addWidget(self.error_banner)

        self.subject_list = QtWidgets.QListWidget(self)
        self.subject_list.currentRowChanged.connect(self._on_row_selected)
        layout.addWidget(self.subject_list, stretch=1)

        self.new_patient_btn = QtWidgets.QPushButton('New Patient...', self)
        self.new_patient_btn.clicked.connect(self.newPatientRequested.emit)
        layout.addWidget(self.new_patient_btn)

        action_row = QtWidgets.QHBoxLayout()
        self.delete_btn = QtWidgets.QPushButton('Delete', self)
        self.delete_btn.clicked.connect(self.delete_selected)
        action_row.addWidget(self.delete_btn)
        self.refresh_btn = QtWidgets.QPushButton('Refresh', self)
        self.refresh_btn.clicked.connect(self.poll_thread.refresh_now)
        action_row.addWidget(self.refresh_btn)
        layout.addLayout(action_row)

        self.setWidget(container)

    def _on_subjects_updated(self, subjects):
        self.error_banner.hide()
        self._subjects_by_row = subjects
        current_subject = self.app_state.subject
        self._apply_filter()
        if current_subject:
            for row, s in enumerate(self._subjects_by_row):
                if s['id'] == current_subject['id']:
                    self.subject_list.setCurrentRow(self._visible_row_for(row))
                    break

    def _visible_row_for(self, data_row):
        for i in range(self.subject_list.count()):
            if self.subject_list.item(i).data(Qt.UserRole) == self._subjects_by_row[data_row]['id']:
                return i
        return -1

    def _apply_filter(self):
        text = self.filter_edit.text().strip().lower()
        self.subject_list.blockSignals(True)
        self.subject_list.clear()
        for s in self._subjects_by_row:
            if text and text not in s['name'].lower():
                continue
            item = QtWidgets.QListWidgetItem(f"{s['name']} (#{s['id']})")
            item.setData(Qt.UserRole, s['id'])
            self.subject_list.addItem(item)
        self.subject_list.blockSignals(False)

    def _on_refresh_failed(self, msg):
        self.error_banner.setText(f"Can't refresh patients -- server unreachable:\n{msg}")
        self.error_banner.show()

    def _on_row_selected(self, row):
        if row < 0:
            self.app_state.set_subject(None)
            return
        subject_id = self.subject_list.item(row).data(Qt.UserRole)
        subject = next((s for s in self._subjects_by_row if s['id'] == subject_id), None)
        self.app_state.set_subject(subject)

    def delete_selected(self):
        subject = self.app_state.subject
        if not subject:
            QtWidgets.QMessageBox.warning(self, '', 'Select a subject first.')
            return
        reply = QtWidgets.QMessageBox.question(
            self, 'Confirm delete', f"Delete subject '{subject['name']}'? This cannot be undone.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
        if reply != QtWidgets.QMessageBox.Yes:
            return
        try:
            self.app_state.api.delete_subject(subject['id'])
        except (ApiError, Exception) as e:
            QtWidgets.QMessageBox.critical(self, '', f'Failed to delete subject:\n{e}')
            return
        self.app_state.set_subject(None)
        self.poll_thread.refresh_now()

    def shutdown(self):
        self.poll_thread.stop()
