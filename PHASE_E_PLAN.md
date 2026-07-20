# Phase (e): Unified Qt UI — Detailed Plan

Scope: replace `v2/client/client_main.py`'s launcher-plus-5-independent-windows model with a
single `QMainWindow`. This file is the itemized plan for that one phase; see [PLAN.md](PLAN.md)
for how it fits into the overall v2 re-architecture. Nothing in `BrainQuake/` (legacy) or
`v2/server/` changes for this phase.

## Requirements (from user)

- Single unified window, responsive and interactive.
- All server errors — including the server simply not responding — must be clearly visible to
  the user, not just logged.
- Tabs, in this order:
  1. MRI/CT Surface Reconstruction
  2. Electrodes Extraction
  3. Ictal module
  4. Interictal module
  5. Visualization / Results
- A side panel for patients (subjects) and a bottom panel for server jobs/status.

## Decisions locked in this session

These were open design forks with real effort/risk implications; resolved by the user before
writing this plan so the checklist below reflects one concrete design, not options.

1. **Mayavi 3D views are embedded in the window**, not left as pop-out `mlab.show()` windows.
   This is the highest-risk item in this phase (see Risks below) — it moves Phase (e) out of the
   "mechanical, reuses existing widgets as-is" category PLAN.md originally estimated it as.
2. **All 5 tabs are constructed eagerly at startup**, kept alive for the app's lifetime (mirrors
   how these widgets already manage their own state today; simplest model for broadcasting
   "current subject changed" to every tab at once).
3. **Tab 5 ("Visualization / Results") is the existing SOZ module, renamed** — not a new combined
   dashboard. Its embedded mayavi fusion view is still new work (see decision 1); the surrounding
   widget/table logic is otherwise unchanged from `client_soz.py`.

## Architecture overview

```
v2/client/
  main_window.py        # NEW — QMainWindow: menu bar, status bar, central QTabWidget,
                         #   left PatientsPanel dock, bottom JobsPanel dock
  app_state.py           # NEW — holds current ApiClient + current subject; emits
                         #   subjectChanged(dict|None); single source of truth tabs subscribe to
  patients_panel.py      # NEW — QDockWidget (left): subject list, create/delete, search
  jobs_panel.py          # NEW — QDockWidget (bottom): live job table + expandable log tail
  connection_monitor.py  # NEW — background QThread polling GET / on an interval; drives the
                         #   status-bar connection indicator and gates panels' "can't reach
                         #   server" banners
  mayavi_view.py         # NEW — reusable embeddable mayavi scene widget (traitsui Scene editor
                         #   wrapped as a QWidget), used by surf/elec/soz tabs instead of
                         #   `from mayavi import mlab; mlab.show()`
  client_surf.py         # MODIFIED — embed pial preview via mayavi_view.py; add set_subject()
  client_elec.py         # MODIFIED — embed contacts/vis3D view via mayavi_view.py; add set_subject()
  client_ictal.py        # MODIFIED — add set_subject(); no mayavi involved (matplotlib only)
  client_inter.py        # MODIFIED — add set_subject(); no mayavi involved
  client_soz.py          # MODIFIED — embed fusion view via mayavi_view.py; add set_subject()
  client_main.py          # RETIRED — fully superseded by main_window.py (see checklist note)
```

## Component details

### 1. `app_state.py` — shared subject/session state

Today each tab widget receives `(api, subject)` once at construction time from
`client_main.py`'s button handlers, which create a *new* tab widget per click. With eager,
persistent tabs (decision 2) there is no "construction time" moment to pass the current subject —
subject selection now happens continuously from the Patients panel while tabs already exist.

- `AppState(QObject)`: holds `api: ApiClient`, `subject: dict | None`; emits
  `subjectChanged = pyqtSignal(object)` on change; emits `serverUrlChanged = pyqtSignal(str)` when
  the base URL is edited (each tab's `ApiClient` reference is swapped, not rebuilt, per the
  existing `on_server_changed` pattern in `client_main.py:181-186`).
- Every tab widget gets a new `set_subject(self, subject)` method (replacing the constructor-time
  `self.subject = subject`) that updates its internal state and refreshes anything subject-scoped
  (artifact lists, job history). `main_window.py` connects `AppState.subjectChanged` to all 5
  tabs' `set_subject` slots plus `JobsPanel`'s subject filter.
- Tabs must tolerate `subject is None` at startup (no subject selected yet) — every action button
  that requires a subject shows an inline "select or create a subject first" state instead of the
  current `QMessageBox.warning` (that dialog pattern is fine for a direct click with no subject,
  but the tab should also passively disable/gray those actions when `subject is None`, since the
  window is not launched fresh-per-task anymore — it may sit idle with no subject selected for a
  while).

### 2. `patients_panel.py` — left dock

- `QDockWidget("Patients", ...)` docked `Qt.LeftDockWidgetArea`, non-closable (or re-openable from
  a View menu if closed).
- Contents: search/filter `QLineEdit` over subject name, `QListWidget` (or `QTreeWidget` if
  hospital/recon-type columns are wanted) populated from `api.list_subjects()`, "New Subject" +
  "Delete Subject" buttons, a manual "Refresh" button.
- Selecting a row sets `AppState.subject` (emits `subjectChanged`).
- Refresh policy: on-demand (button, and after create/delete) **plus** a background poll every
  ~10s so a subject created from another machine/session shows up — reuses the same
  `ConnectionMonitor`-driven pattern as the jobs panel (see Threading model below); must not block
  the GUI thread.
- Delete needs a confirmation dialog (destructive, matches the general-purpose destructive-action
  guidance) and must clear `AppState.subject` if the deleted subject was selected.

### 3. `jobs_panel.py` — bottom dock

- `QDockWidget("Jobs", ...)` docked `Qt.BottomDockWidgetArea`.
- `QTableWidget` columns: ID, Subject, Type, State (color-coded text: queued=gray, running=blue,
  finished=green, failed=red, cancelled=orange), Progress (`QProgressBar` per row via
  `setCellWidget`), Created, Actions (Cancel button, shown only while `queued`/`running`; View Log
  button always).
- "View Log" expands an inline `QTextEdit` (or opens a small non-modal dialog) showing
  `GET /jobs/{id}/log`, auto-refreshing on the same poll cycle while that job is non-terminal.
- Filter toggle: "All subjects" vs "current subject only" (default: current subject, since that's
  the common case; global visibility is what makes this dock genuinely useful over the per-tab
  progress bars that already exist).
- Poll interval: every 2s while any job is non-terminal, backing off to ~10s when nothing is
  in-flight — implemented as a background `QThread` (see Threading model), not a GUI-thread
  `QTimer` calling `requests` directly.
- Cancel button calls `api.cancel_job(id)`; on `ApiError`, show inline error in that row (not a
  modal) — cancelling is opportunistic and failure isn't catastrophic.

### 4. `connection_monitor.py` — server reachability

This directly satisfies the "server not responding must be clearly visible" requirement, which
today is only a `logger.warning(...)` in `client_main.py:192` and `client_surf.py:148` — invisible
unless someone's watching stdout.

- `ConnectionMonitor(QThread)`: loop with a short interval (~5s), each iteration does
  `GET /` (the existing FastAPI root endpoint, `v2/server/app/main.py:34`) with a **short**
  timeout (2-3s, distinct from `ApiClient`'s default 30s — a health check must fail fast, not hang
  the indicator for 30s while the rest of the UI is fine).
  Emits `statusChanged(ok: bool, detail: str)`.
- `main_window.py` shows this in a permanent `QStatusBar` widget: a colored dot + text — green
  "Connected — http://host:port" / red "Server unreachable — <error>" (connection refused, DNS
  failure, and timeout should render distinct, human-readable text, not a raw stack trace).
- `PatientsPanel` and `JobsPanel` both subscribe to `statusChanged` too, so their own background
  refreshes show an inline "can't refresh — server unreachable" banner in the panel itself instead
  of silently doing nothing (fixes `refresh_subjects`'s current silent-return behavior at
  `client_main.py:188-193` and `client_surf.py:144-149`).
- Error-surfacing policy going forward (audit existing tabs against this):
  - **User-initiated action fails** (upload, compute, recon) → keep the existing `QMessageBox`
    pattern (`uploadFailed`, etc.) — a direct, expected-synchronous consequence of a click.
  - **Background/passive refresh fails** (patient list poll, jobs poll, health check) → inline
    banner in the relevant panel + status bar, never a modal (modals stacking from a background
    timer while the user is mid-task elsewhere is its own bug).

### 5. `mayavi_view.py` — embedded scene widget

Mayavi's actual Qt embedding mechanism is TraitsUI's `Scene` editor (which itself wraps a
`QVTKRenderWindowInteractor` internally) — not a raw `QVTKRenderWindowInteractor` built by hand.
Concretely:

```python
from traits.api import HasTraits, Instance
from traitsui.api import View, Item
from mayavi.core.ui.api import MayaviScene, MlabSceneModel, SceneEditor

class _MayaviScene(HasTraits):
    scene = Instance(MlabSceneModel, ())
    view = View(Item('scene', editor=SceneEditor(scene_class=MayaviScene),
                      show_label=False, resizable=True))

class MayaviView(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene_model = _MayaviScene()
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self._scene_model.edit_traits(
            parent=self, kind='subpanel').control)

    @property
    def mlab(self):
        return self._scene_model.scene.mlab   # bound to THIS scene, not the mlab global
```

- Each embedding site (recon preview, electrode contacts, SOZ fusion) gets its **own**
  `MayaviView` instance / own `MlabSceneModel` — do not share one global `mlab` figure across
  tabs, which is what the current pop-out code implicitly does (`from mayavi import mlab` at
  module scope in `client_surf.py:19`, `client_elec.py`, `soz_result.py`). Cross-tab figure
  collisions are the most likely source of subtle embedding bugs here.
  - `client_surf.py`'s `mayaviplot()` (line 185-203): replace `mlab.triangular_mesh(...)`
    /`mlab.draw()`/`mlab.show()` with `self.mayavi_view.mlab.triangular_mesh(...)` — no `mlab.show()`
    call at all once embedded (that call starts/blocks on GUI's own event loop, which is already
    running as the main Qt app).
  - `client_elec.py`'s vis3D-equivalent and `client_soz.py`'s fusion view: same substitution
    pattern.
- Requires `ETS_TOOLKIT` (or the newer `ETSConfig.toolkit`) pinned to `'qt4'` (works with PyQt5 in
  current mayavi/traitsui releases) **before** `mayavi`/`traits` are imported anywhere in the
  process — set this at the very top of `main_window.py`, ahead of any tab module import, since
  import order determines which toolkit ETS binds to.
- Tab-switch/resize behavior needs manual smoke-testing: embedded VTK render widgets inside a
  `QTabWidget` are known to sometimes need an explicit `scene.render()` nudge after the tab
  becomes visible again (inactive-tab render-context suspension varies by platform/driver) — treat
  this as a concrete test case in Validation below, not an assumption.

### 6. `main_window.py` — shell

- `QMainWindow` subclass. Menu bar: `File > Server settings…` (dialog wrapping today's URL field
  behavior), `File > Quit`; `View > Patients panel`, `View > Jobs panel` (toggle dock visibility,
  in case the user closes one). `QStatusBar` hosts the connection indicator (component 4).
- Central widget: `QTabWidget` with the 5 tabs added in the exact order specified, each `addTab`
  call using the requested label text (note: labels differ from the existing class/window titles —
  e.g. tab 1 is "MRI/CT Surface Reconstruction" though the class stays `reconSurferUi`, tab 5 is
  "Visualization / Results" though the class stays `SOZResultModule` — only the tab label changes,
  not the underlying widget names, per decision 3).
- Constructs `AppState`, `ConnectionMonitor`, all 5 tab widgets, `PatientsPanel`, `JobsPanel` in
  `__init__`; wires every signal described above before `self.show()`.
- Window close: if any tab has an in-flight `QThread` (upload/recon/compute), warn before closing
  rather than killing threads silently — mirrors the "confirm before hard-to-reverse actions"
  guidance; a mid-upload or mid-recon-request quit is exactly this kind of case (though the
  recon job itself keeps running server-side either way, since it's not owned by the client
  thread).

## Threading model (must hold across every new component)

The core "server not responding must be visible" requirement fails if any polling loop runs
`requests` calls directly from a GUI-thread `QTimer.timeout` slot — a hung/slow server would then
freeze the whole window (buttons, tab switches, everything) for up to the request's timeout, which
is the opposite of "responsive." Every one of these polling responsibilities must run in a
dedicated background `QThread` that only emits Qt signals back to the GUI thread:

- `ConnectionMonitor` (health check)
- `PatientsPanel`'s background subject-list refresh
- `JobsPanel`'s job-table refresh
- (Existing) per-tab `QThread`s for upload/recon/compute/segment — already follow this pattern
  (`UploadAndReconThread`, `EiComputeThread`, `HiComputeThread`, `SozFuseThread`, `JobPollThread`)
  and don't need rework, just reuse.

## File-level checklist

- [x] `v2/client/app_state.py` — `AppState(QObject)`: `api`, `subject`, `subjectChanged` signal,
      `apiChanged` signal
- [x] `v2/client/connection_monitor.py` — `ConnectionMonitor(QThread)`: short-timeout `GET /` poll
      loop, `statusChanged(bool, str)` signal
- [x] `v2/client/mayavi_view.py` — `MayaviView(QWidget)` reusable embeddable scene, per component 5
- [x] `v2/client/patients_panel.py` — `PatientsPanel(QDockWidget)`: list/search/create/delete,
      background refresh via its own `SubjectPollThread` (kept separate from `ConnectionMonitor` —
      simpler ownership, each dock owns its own polling)
- [x] `v2/client/jobs_panel.py` — `JobsPanel(QDockWidget)`: job table, progress bars, log tail,
      cancel button, current-subject/all-subjects filter toggle
- [x] `v2/client/main_window.py` — the `QMainWindow` shell described above; new app entry point
      (`if __name__ == '__main__':`)
- [x] `v2/client/client_surf.py` — added `set_subject()` + `attach_mayavi_view()`; `mayaviplot()`
      draws into the attached `MayaviView` when present, falls back to pop-out `mlab.show()`
      otherwise (used by the `__main__` standalone block)
- [x] `v2/client/client_elec.py` — added `set_subject()` + `attach_mayavi_view()`; `vis3D()` same
      embedded/fallback split; constructor now tolerates `subject=None` (Import Surf stays
      disabled until one is selected)
- [x] `v2/client/client_ictal.py` — added `set_subject()`; constructor tolerates `subject=None`
      (all trace-viewer buttons disabled until a subject is selected)
- [x] `v2/client/client_inter.py` — added `set_subject()`, same treatment as ictal
- [x] `v2/client/client_soz.py` — added `set_subject()`; embeds `MayaviView` side-by-side with the
      results table in a `QSplitter` (self-contained, built its own layout already so no
      wrapper-splitter needed at the main_window level, unlike surf/elec)
- [x] Retire `v2/client/client_main.py` — user confirmed; deleted (superseded by `main_window.py`)
- [x] Reconciled `client_surf.py`'s subject handling: `UploadAndReconThread` now accepts an
      optional `subject_id` set from the globally-selected subject (via `set_subject()`); if none
      is selected it falls back to the legacy create-or-reuse-by-filename behavior. Verified via
      the live smoke test below (selecting "SmokeTestPatient" propagated into `recon_tab.subject`
      correctly).

## Smoke-test results (this implementation pass)

Ran against a live `v2/server` instance (`uvicorn`, throwaway subject, cleaned up afterward —
config.json/db reverted, no pollution of real dev data left behind):

- MainWindow constructs, shows all 5 tabs in the specified order/labels, both docks attach
  correctly.
- Creating a subject via the API and refreshing the Patients panel populates it; selecting a row
  propagated the same subject dict into `app_state.subject` and all 5 tabs' `.subject` correctly,
  including enabling previously-disabled buttons (Electrodes' "Import Surf data", SOZ's "Fuse
  EI+HI").
- Killing the server mid-session: status bar turned red ("Server unreachable (connection
  refused)") within one poll cycle, Patients and Jobs panel both showed their inline banners, and
  a tab switch performed **during** the outage completed in ~39ms — confirms the background-
  QThread polling design (§ Threading model) actually keeps the GUI responsive while the server is
  down, not just in theory.
- Injected synthetic job rows directly into `JobsPanel`: table renders, state-color-coding works,
  opening a log dialog for a nonexistent job doesn't crash (shows the fetch error inline instead).
- **Found and fixed a real bug during smoke-testing**: `PatientsPanel.__init__` originally called
  `_init_ui()` (which wires the Refresh button to `self.poll_thread.refresh_now`) *before*
  `self.poll_thread` was constructed — `AttributeError` on startup. Fixed by constructing the poll
  thread first.
- **Confirmed risk #2 from the Risks section, with a fix**: the embedded `MayaviView` (TraitsUI's
  `SceneEditor`) segfaults during Python/VTK teardown at process exit on this machine — isolated by
  testing a bare pop-out `mlab.show()` window on the same machine, which does *not* crash on exit,
  so the crash is specific to the embedded-widget teardown path, not mayavi/VTK in general here.
  During actual use (construction, display, redraws) nothing crashed. Mitigated in
  `main_window.py`'s `__main__` block with `os._exit(0)` after `app.exec_()` returns, instead of
  `sys.exit(app.exec_())` — safe since all user data is already persisted server-side by the time
  the window closes; this just skips Python's crash-prone finalization. **This should be re-tested
  on whatever machine actually runs this in production** — it may be specific to this VTK
  9.4.2/PyQt5 5.15/Windows combination.

## Risks

1. **Mayavi embedding is genuinely new integration work, not a reuse of existing code as originally
   scoped.** PLAN.md's Phase (e) estimate (1.5-2 weeks, "moderate — reuses existing widgets
   largely as-is") assumed pop-out `mlab.show()` windows stayed unchanged. Embedding 3 independent
   `MlabSceneModel` scenes inside `QTabWidget` pages, correctly isolated from each other and from
   matplotlib's own Qt5Agg canvases in the same process, is close-to-untested territory for this
   codebase. **Re-estimate this phase as M-L (2.5-4 weeks)**, not the original M (1.5-2 weeks),
   with the embedding work as the long pole.
2. **ETS/toolkit binding conflicts.** `matplotlib` (Qt5Agg backend, used in ictal/interictal tabs)
   and `mayavi`/`traitsui` (Qt backend via ETS) both need to agree on PyQt5 as the toolkit and
   share one `QApplication` event loop cleanly. Import-order sensitivity here is a real footgun —
   `main_window.py` must set `ETSConfig.toolkit` before any tab module (which imports `mayavi`) is
   imported.
3. **Tab-switch render suspension.** Inactive-tab VTK contexts occasionally need a manual
   `scene.render()` nudge on becoming visible again — needs explicit smoke-testing per tab, not an
   assumption that "it'll just repaint."
4. **Subject-state propagation timing.** Since tabs are eager/persistent (decision 2) but the recon
   tab manages its own subject creation, there's a window where a tab is holding stale subject
   data if `AppState.subject` changes while that tab is mid-operation (e.g. a recon upload in
   progress when the user switches the Patients panel selection). Decide whether tabs snapshot
   their subject at operation-start (safer — don't let a global switch yank state out from under
   an in-flight thread) rather than always reading `AppState.subject` live.

## Validation / done-criteria for this phase

- [ ] App launches to one `QMainWindow`, 5 tabs in the specified order and labels, Patients dock
      left, Jobs dock bottom.
- [ ] Killing the server process (or pointing the URL at an unreachable host) turns the status bar
      indicator red with a readable message within one poll interval, and does **not** freeze any
      tab, button, or panel — verified by clicking around the UI while the server is down.
- [ ] Creating/selecting/deleting a subject in the Patients panel updates all 5 tabs' subject
      context without needing to reopen anything.
- [ ] Starting a recon job (or any compute job) shows up in the Jobs panel with a live progress
      bar and updates to completion; cancel button works on a queued/running job.
- [ ] Each embedded mayavi view (recon pial preview, electrode contacts, SOZ fusion) renders
      correctly the first time its tab is opened, and again after switching away and back — this
      second check specifically covers risk #3 above.
- [ ] Manual side-by-side comparison against the current 5-separate-window behavior confirms no
      functional regression in any tab's existing actions (upload, detect/segment, EI/HFO compute,
      SOZ fuse), per the same "no automated diff, manual comparison" approach used for Phase (b).
