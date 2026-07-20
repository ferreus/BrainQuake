"""Shared session state for the unified main window (Phase (e), PHASE_E_PLAN.md §1).

Before this, each tab widget received `(api, subject)` once at construction time from
client_main.py's button handlers, which created a *new* tab widget per click. With the
unified window all 5 tabs are constructed once and kept alive for the app's lifetime, so
there is no "construction time" moment to hand over the current subject -- subject
selection now happens continuously from the Patients panel while tabs already exist.
AppState is the single source of truth every tab/panel subscribes to instead.
"""
from PyQt5.QtCore import QObject, pyqtSignal


class AppState(QObject):
    subjectChanged = pyqtSignal(object)   # emits the new subject dict, or None
    apiChanged = pyqtSignal(object)       # emits the new ApiClient instance

    def __init__(self, api, parent=None):
        super().__init__(parent)
        self._api = api
        self._subject = None

    @property
    def api(self):
        return self._api

    def set_api(self, api):
        self._api = api
        self.apiChanged.emit(api)

    @property
    def subject(self):
        return self._subject

    def set_subject(self, subject):
        if subject == self._subject:
            return
        self._subject = subject
        self.subjectChanged.emit(subject)
