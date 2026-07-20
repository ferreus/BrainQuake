"""Reusable embeddable mayavi scene widget (PHASE_E_PLAN.md §5).

Replaces the pop-out `from mayavi import mlab; mlab.show()` pattern used by the legacy
per-window client_surf.py/client_elec.py/soz_result.py with a widget that can be added
straight into a QTabWidget page. Mayavi's actual Qt embedding mechanism is TraitsUI's
`Scene` editor (which itself wraps a QVTKRenderWindowInteractor internally), not a
raw QVTKRenderWindowInteractor built by hand -- MayaviView hides that behind a plain
QWidget so callers just do `some_layout.addWidget(MayaviView())` and then draw via
`.mlab`, `.scene`.

IMPORTANT: this module must be imported before anything else in the app imports
`mayavi`/`traits` (client_surf.py, client_elec.py, client_soz.py all do a module-level
`from mayavi import mlab`), because ETSConfig's toolkit binds to whichever toolkit is
active at first import and is not changeable afterwards. main_window.py imports this
module first for exactly that reason -- don't reorder those imports.

Each call site (recon pial preview, electrode contacts/vis3D, SOZ fusion view) must
construct its OWN MayaviView / own MlabSceneModel -- never share one global `mlab`
figure across tabs (that's what the legacy pop-out code implicitly does via the module
-level `mlab` singleton). Cross-tab figure collisions are the most likely source of
subtle embedding bugs here.
"""
import os

os.environ.setdefault('ETS_TOOLKIT', 'qt4')
os.environ.setdefault('QT_API', 'pyqt5')

from PyQt5 import QtWidgets
from traits.api import HasTraits, Instance
from traitsui.api import View, Item
from mayavi.core.ui.api import MayaviScene, MlabSceneModel, SceneEditor


class _SceneHolder(HasTraits):
    scene = Instance(MlabSceneModel, ())

    view = View(
        Item('scene', editor=SceneEditor(scene_class=MayaviScene),
             show_label=False, resizable=True),
        resizable=True,
    )


class MayaviView(QtWidgets.QWidget):
    """An embeddable mayavi scene. Use `.mlab` for drawing calls (mlab.triangular_mesh,
    mlab.points3d, ...) scoped to this widget's own scene -- never the `mayavi.mlab`
    module-level singleton. Never call `mlab.show()` on it: the Qt event loop already
    owns rendering once this widget is on-screen."""

    def __init__(self, parent=None, bgcolor=(0.8, 0.8, 0.8)):
        super().__init__(parent)
        self._holder = _SceneHolder()
        self.scene = self._holder.scene
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        control = self._holder.edit_traits(parent=self, kind='subpanel').control
        layout.addWidget(control)
        self.scene.background = bgcolor

    @property
    def mlab(self):
        return self.scene.mlab

    def clear(self):
        self.mlab.clf()

    def render(self):
        """Force a repaint -- some platforms suspend an inactive tab's VTK render
        context, so callers should call this after their tab becomes visible again
        (see PHASE_E_PLAN.md risk #3), not just after drawing."""
        self.scene.render()
