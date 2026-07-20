"""Jobs/Logs bottom dock (PHASE_E_PLAN.md §3).

Live table of jobs across (or scoped to) the current subject, each with a progress bar,
cancel button, and an expandable log tail. Polling runs on its own QThread
(JobsPollThread) -- never a GUI-thread QTimer calling `requests` directly -- backing off
from a 2s cycle (while anything is in flight) to a 10s idle cycle, so a hung server
can't freeze the window and idle polling doesn't hammer it either.
"""
import threading
import logging

from PyQt5 import QtWidgets, QtCore, QtGui
from PyQt5.QtCore import QThread, pyqtSignal, Qt

from api_client import ApiError, TERMINAL_JOB_STATES

logger = logging.getLogger(__name__)

BUSY_INTERVAL = 2.0
IDLE_INTERVAL = 10.0

STATE_COLORS = {
    'queued': '#808080',
    'running': '#1565c0',
    'finished': '#2e7d32',
    'failed': '#c62828',
    'cancelled': '#e65100',
}


class JobsPollThread(QThread):
    jobsUpdated = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, get_api, get_subject_id, parent=None):
        super().__init__(parent)
        self._get_api = get_api
        self._get_subject_id = get_subject_id  # callable() -> int|None
        self._stop = False
        self._wake = threading.Event()

    def stop(self):
        self._stop = True
        self._wake.set()
        self.wait(int(BUSY_INTERVAL * 1000) + 1000)

    def refresh_now(self):
        self._wake.set()

    def run(self):
        while not self._stop:
            interval = IDLE_INTERVAL
            try:
                jobs = self._get_api().list_jobs(subject_id=self._get_subject_id())
                self.jobsUpdated.emit(jobs)
                if any(j['state'] not in TERMINAL_JOB_STATES for j in jobs):
                    interval = BUSY_INTERVAL
            except (ApiError, Exception) as e:
                self.failed.emit(str(e))
            self._wake.wait(interval)
            self._wake.clear()


class LogFetchThread(QThread):
    done = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, api, job_id, parent=None):
        super().__init__(parent)
        self.api = api
        self.job_id = job_id

    def run(self):
        try:
            text = self.api.get_job_log(self.job_id)
            self.done.emit(text)
        except (ApiError, Exception) as e:
            self.failed.emit(str(e))


class LogDialog(QtWidgets.QDialog):
    def __init__(self, api, job, parent=None):
        super().__init__(parent)
        self.api = api
        self.job = job
        self.setWindowTitle(f"Job #{job['id']} log ({job['job_type']})")
        self.resize(700, 450)
        layout = QtWidgets.QVBoxLayout(self)
        self.text = QtWidgets.QTextEdit(self)
        self.text.setReadOnly(True)
        self.text.setFontFamily('Consolas')
        layout.addWidget(self.text)
        refresh_btn = QtWidgets.QPushButton('Refresh', self)
        refresh_btn.clicked.connect(self.refresh)
        layout.addWidget(refresh_btn)

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(int(BUSY_INTERVAL * 1000))
        self._timer.timeout.connect(self._auto_refresh)
        self._fetch_thread = None
        self.refresh()
        if job['state'] not in TERMINAL_JOB_STATES:
            self._timer.start()

    def _auto_refresh(self):
        if self.job['state'] in TERMINAL_JOB_STATES:
            self._timer.stop()
        self.refresh()

    def refresh(self):
        if self._fetch_thread and self._fetch_thread.isRunning():
            return
        self._fetch_thread = LogFetchThread(self.api, self.job['id'])
        self._fetch_thread.done.connect(self._on_log)
        self._fetch_thread.failed.connect(self._on_log_failed)
        self._fetch_thread.start()

    def _on_log(self, text):
        scrollbar = self.text.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 4
        self.text.setPlainText(text)
        if at_bottom:
            scrollbar.setValue(scrollbar.maximum())

    def _on_log_failed(self, msg):
        self.text.setPlainText(f"[could not fetch log]\n{msg}")

    def closeEvent(self, event):
        self._timer.stop()
        super().closeEvent(event)


class JobsPanel(QtWidgets.QDockWidget):
    COLUMNS = ['ID', 'Subject', 'Type', 'State', 'Progress', 'Created', '']

    def __init__(self, app_state, parent=None):
        super().__init__('Jobs', parent)
        self.app_state = app_state
        self.setAllowedAreas(Qt.BottomDockWidgetArea | Qt.TopDockWidgetArea)
        self.setFeatures(QtWidgets.QDockWidget.DockWidgetMovable | QtWidgets.QDockWidget.DockWidgetFloatable)

        self._current_subject_only = True
        self._jobs = []
        self._init_ui()

        self.poll_thread = JobsPollThread(
            get_api=lambda: self.app_state.api,
            get_subject_id=self._filter_subject_id)
        self.poll_thread.jobsUpdated.connect(self._on_jobs_updated)
        self.poll_thread.failed.connect(self._on_refresh_failed)
        self.poll_thread.start()

        self.app_state.subjectChanged.connect(lambda _s: self.poll_thread.refresh_now())
        self.app_state.apiChanged.connect(lambda _api: self.poll_thread.refresh_now())

    def _filter_subject_id(self):
        if self._current_subject_only and self.app_state.subject:
            return self.app_state.subject['id']
        return None

    def _init_ui(self):
        container = QtWidgets.QWidget(self)
        layout = QtWidgets.QVBoxLayout(container)

        top_row = QtWidgets.QHBoxLayout()
        self.scope_checkbox = QtWidgets.QCheckBox('Current subject only', self)
        self.scope_checkbox.setChecked(True)
        self.scope_checkbox.toggled.connect(self._on_scope_toggled)
        top_row.addWidget(self.scope_checkbox)
        top_row.addStretch(1)
        self.error_banner = QtWidgets.QLabel(self)
        self.error_banner.setStyleSheet('color: white; background-color: #b00020; padding: 2px 6px; border-radius: 3px;')
        self.error_banner.hide()
        top_row.addWidget(self.error_banner)
        layout.addLayout(top_row)

        self.table = QtWidgets.QTableWidget(self)
        self.table.setColumnCount(len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        layout.addWidget(self.table)

        self.setWidget(container)

    def _on_scope_toggled(self, checked):
        self._current_subject_only = checked
        self.poll_thread.refresh_now()

    def _on_refresh_failed(self, msg):
        self.error_banner.setText(f"Can't refresh jobs -- server unreachable: {msg}")
        self.error_banner.show()

    def _on_jobs_updated(self, jobs):
        self.error_banner.hide()
        self._jobs = jobs
        self.table.setRowCount(0)
        for job in jobs:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(str(job['id'])))
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(job.get('subject_id', ''))))
            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(job['job_type']))

            state_item = QtWidgets.QTableWidgetItem(job['state'])
            color = STATE_COLORS.get(job['state'], '#000000')
            state_item.setForeground(QtGui.QBrush(QtGui.QColor(color)))
            self.table.setItem(row, 3, state_item)

            progress = QtWidgets.QProgressBar()
            progress.setValue(int(job.get('progress_pct') or 0))
            self.table.setCellWidget(row, 4, progress)

            self.table.setItem(row, 5, QtWidgets.QTableWidgetItem(str(job.get('created_at', ''))))

            actions = QtWidgets.QWidget()
            actions_layout = QtWidgets.QHBoxLayout(actions)
            actions_layout.setContentsMargins(0, 0, 0, 0)
            log_btn = QtWidgets.QPushButton('Log')
            log_btn.clicked.connect(lambda _checked, j=job: self._show_log(j))
            actions_layout.addWidget(log_btn)
            if job['state'] in ('queued', 'running'):
                cancel_btn = QtWidgets.QPushButton('Cancel')
                cancel_btn.clicked.connect(lambda _checked, j=job: self._cancel(j))
                actions_layout.addWidget(cancel_btn)
            self.table.setCellWidget(row, 6, actions)

    def _show_log(self, job):
        dlg = LogDialog(self.app_state.api, job, parent=self)
        dlg.show()

    def _cancel(self, job):
        try:
            self.app_state.api.cancel_job(job['id'])
        except (ApiError, Exception) as e:
            QtWidgets.QMessageBox.warning(self, '', f"Could not cancel job #{job['id']}:\n{e}")
            return
        self.poll_thread.refresh_now()

    def shutdown(self):
        self.poll_thread.stop()
