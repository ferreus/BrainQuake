"""Jobs/Logs bottom dock (PHASE_E_PLAN.md §3, extended for the "New Patient" upload flow).

Live table of jobs across (or scoped to) the current subject, each with a progress bar,
cancel button, and an expandable log tail. Polling runs on its own QThread
(JobsPollThread) -- never a GUI-thread QTimer calling `requests` directly -- backing off
from a 2s cycle (while anything is in flight) to a 10s idle cycle, so a hung server
can't freeze the window and idle polling doesn't hammer it either.

Also renders "pending" entries that aren't real server-side Job rows yet -- the file
upload phase of the New Patient dialog (new_patient_dialog.py) has no job row on the
server until the upload finishes and the recon job is queued, but the user still needs
to see *something* with a live progress bar the moment they click Upload. The dialog
closes immediately on click (see new_patient_dialog.py), so this panel is the only
place upload failures ever become visible -- a pending row's "failed" state must stay
on screen with the error message until the user dismisses it, not disappear silently.
"""
import threading
import itertools
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
    'uploading': '#1565c0',
    'starting': '#1565c0',
}

_pending_id_seq = itertools.count(1)


class PendingJob:
    """A client-side-only row shown in the Jobs table before a real server Job
    exists -- see module docstring. `cancel_event`, if given, is a threading.Event
    the owning upload thread polls; setting it (via the panel's Cancel button)
    signals that thread to abort."""

    def __init__(self, subject_id, subject_name, label, cancel_event=None):
        self.id = f"pending-{next(_pending_id_seq)}"
        self.subject_id = subject_id
        self.subject_name = subject_name
        self.label = label
        self.state = 'uploading'
        self.progress_pct = 0.0
        self.message = ''
        self.cancel_event = cancel_event


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
        self._last_jobs = []
        self._pending = {}  # id -> PendingJob, insertion order preserved (dict, py3.7+)
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
        self._last_jobs = jobs
        self._rebuild_table()

    # -- pending (client-side, pre-job) rows -----------------------------------------

    def add_pending(self, subject_id, subject_name, label='upload', cancel_event=None):
        """Adds a pending row and returns its id. Caller drives it forward with
        update_pending()/remove_pending() -- there is no automatic timeout or
        server-side backing for this entry."""
        pending = PendingJob(subject_id, subject_name, label, cancel_event=cancel_event)
        self._pending[pending.id] = pending
        self._rebuild_table()
        return pending.id

    def update_pending(self, pending_id, progress_pct=None, message=None, state=None):
        pending = self._pending.get(pending_id)
        if pending is None:
            return
        if progress_pct is not None:
            pending.progress_pct = progress_pct
        if message is not None:
            pending.message = message
        if state is not None:
            pending.state = state
        self._rebuild_table()

    def remove_pending(self, pending_id):
        if self._pending.pop(pending_id, None) is not None:
            self._rebuild_table()

    # -- rendering -----------------------------------------

    def _rebuild_table(self):
        self.table.setRowCount(0)
        for pending in self._pending.values():
            self._append_pending_row(pending)
        for job in self._last_jobs:
            self._append_job_row(job)

    def _append_pending_row(self, pending):
        row = self.table.rowCount()
        self.table.insertRow(row)
        subject_label = (pending.subject_name if pending.subject_id is None
                         else f"{pending.subject_name} (#{pending.subject_id})")
        self.table.setItem(row, 0, QtWidgets.QTableWidgetItem('--'))
        self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(subject_label))
        self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(pending.label))

        state_item = QtWidgets.QTableWidgetItem(pending.state)
        color = STATE_COLORS.get(pending.state, '#000000')
        state_item.setForeground(QtGui.QBrush(QtGui.QColor(color)))
        if pending.message:
            state_item.setToolTip(pending.message)
        self.table.setItem(row, 3, state_item)

        progress = QtWidgets.QProgressBar()
        progress.setValue(int(pending.progress_pct))
        self.table.setCellWidget(row, 4, progress)

        self.table.setItem(row, 5, QtWidgets.QTableWidgetItem('--'))

        actions = QtWidgets.QWidget()
        actions_layout = QtWidgets.QHBoxLayout(actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        if pending.message:
            info_btn = QtWidgets.QPushButton('Info')
            info_btn.clicked.connect(lambda _checked, p=pending: self._show_pending_message(p))
            actions_layout.addWidget(info_btn)
        if pending.state in ('uploading', 'starting') and pending.cancel_event is not None:
            cancel_btn = QtWidgets.QPushButton('Cancel')
            cancel_btn.clicked.connect(lambda _checked, p=pending: self._cancel_pending(p))
            actions_layout.addWidget(cancel_btn)
        if pending.state in ('failed', 'cancelled'):
            dismiss_btn = QtWidgets.QPushButton('Dismiss')
            dismiss_btn.clicked.connect(lambda _checked, p=pending: self.remove_pending(p.id))
            actions_layout.addWidget(dismiss_btn)
        self.table.setCellWidget(row, 6, actions)

    def _show_pending_message(self, pending):
        QtWidgets.QMessageBox.information(self, '', pending.message or '(no message)')

    def _cancel_pending(self, pending):
        pending.cancel_event.set()

    def _append_job_row(self, job):
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
