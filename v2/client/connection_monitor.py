"""Background server-reachability poller (PHASE_E_PLAN.md §4).

Satisfies the "server not responding must be clearly visible" requirement: today a
failed refresh is only a `logger.warning(...)` call (client_main.py's
`refresh_subjects`, client_surf.py's `refreshSubjects`) -- invisible unless someone is
watching stdout. This runs a short-timeout health check on its own QThread so a hung or
unreachable server never blocks the GUI thread, and reports status via a Qt signal that
the main window's status bar (and any panel that wants to show an inline banner) can
subscribe to.

Deliberately uses its own short `requests` timeout (2-3s) rather than ApiClient's
default 30s -- a health check needs to fail fast, not hang the indicator for 30s while
the rest of the app might be fine.
"""
import requests
from PyQt5.QtCore import QThread, pyqtSignal


HEALTH_TIMEOUT = 3.0


class ConnectionMonitor(QThread):
    statusChanged = pyqtSignal(bool, str)   # (ok, detail)

    def __init__(self, get_base_url, interval=5.0, parent=None):
        super().__init__(parent)
        self._get_base_url = get_base_url  # callable() -> str, so URL changes are picked up live
        self.interval = interval
        self._stop = False
        self._last_ok = None

    def stop(self):
        self._stop = True
        self.wait(int(self.interval * 1000) + 1000)

    def run(self):
        while not self._stop:
            base_url = self._get_base_url()
            ok, detail = self._check(base_url)
            if ok != self._last_ok or True:
                self.statusChanged.emit(ok, detail)
                self._last_ok = ok
            self.msleep(int(self.interval * 1000))

    def _check(self, base_url):
        try:
            resp = requests.get(base_url.rstrip('/') + '/', timeout=HEALTH_TIMEOUT)
            if resp.ok:
                return True, f'Connected -- {base_url}'
            return False, f'Server returned HTTP {resp.status_code} -- {base_url}'
        except requests.exceptions.ConnectTimeout:
            return False, f'Server unreachable (timed out) -- {base_url}'
        except requests.exceptions.ConnectionError:
            return False, f'Server unreachable (connection refused) -- {base_url}'
        except requests.exceptions.RequestException as e:
            return False, f'Server unreachable ({e}) -- {base_url}'
