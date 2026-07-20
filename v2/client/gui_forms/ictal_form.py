#! /usr/bin/python3.7
# -- coding: utf-8 -- **
"""Ictal tab layout -- standard Qt theme (no custom stylesheets/backgrounds), same
redesign rules as new_patient_dialog.py/elec_form.py. No patient-name or subject-dir
fields -- the subject is whatever's selected in the Patients panel, resolved
automatically server-side; there's nothing to browse or type here. The whole content
area is disabled with a status_label explaining why until a subject is selected
(mirrors gui_forms/elec_form.py's readiness gating, just with one state instead of
electrodes' four -- ictal only needs a subject, not a finished reconstruction).
"""

from PyQt5.QtWidgets import QApplication, QSizePolicy, QMessageBox, QWidget, \
    QPushButton, QLineEdit, QDesktopWidget, QGridLayout, QFileDialog, QListWidget, QLabel, QFrame, QGroupBox
from PyQt5.QtCore import Qt, QThread
import PyQt5.QtWidgets as QtWidgets
import PyQt5.QtCore as QtCore

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib import cm
from matplotlib.widgets import Slider

# classes
class PlotCanvas(FigureCanvas):
    def __init__(self, parent=None, width=7, height=5, dpi=100):
        self.fig = Figure(figsize=(width, height), dpi=dpi)
        self.axes = self.fig.add_axes([0.05, 0.1, 0.9, 0.8])
        FigureCanvas.__init__(self, self.fig)
        self.setParent(parent)
        FigureCanvas.setSizePolicy(self,
                                   QSizePolicy.Expanding,
                                   QSizePolicy.Expanding)
        FigureCanvas.updateGeometry(self)
        self.plot()

    def plot(self):
        self.axes.cla()
        self.draw()

class Ictal_gui(object):

    def setupUi(self, IctalModule):

        # main window
        IctalModule.setObjectName("IctalModule")
        self.setWindowTitle('Ictal Computation')
        self.setAttribute(Qt.WA_MacShowFocusRect, 0)
        outer_layout = QGridLayout()

        # status banner -- explains why the page is disabled (no subject selected),
        # or that it's ready, same pattern as gui_forms/elec_form.py
        self.status_label = QLabel(self)
        self.status_label.setWordWrap(True)
        outer_layout.addWidget(self.status_label, 0, 0, 1, 1)

        # everything below lives in this container, toggled enabled/disabled as a
        # whole based on subject selection (see client_ictal.py's set_subject)
        self.content = QtWidgets.QWidget(self)
        outer_layout.addWidget(self.content, 1, 0, 1, 1)
        self.gridlayout = QGridLayout(self.content)

        # canvas
        self.canvas = PlotCanvas(self.content, width=10, height=5)

        self.leftPannelLayout = QGridLayout()
        self.leftPannelLayout.addWidget(self.canvas, 1, 1, 25, 24)

        self.gridlayout.addLayout(self.leftPannelLayout, 1, 1, 1, 1)
        self.canvas.fig.canvas.mpl_connect('button_press_event', self.canvas_press_button)
        self.canvas.fig.canvas.mpl_connect('scroll_event', self.disp_scroll_mouse)

        self.rightPannelLayout = QGridLayout()
        self.gridlayout.addLayout(self.rightPannelLayout, 1, 2, 1, 1)

        self.gridlayout.setColumnStretch(1, 25)
        self.gridlayout.setColumnStretch(2, 4)

        self.button_inputedf = QPushButton('import .edf data', self.content)
        self.button_inputedf.setToolTip('click to input data')
        self.rightPannelLayout.addWidget(self.button_inputedf, 1, 1, 1, 2)
        self.button_inputedf.clicked.connect(self.dialog_inputedfdata)
        self.button_inputedf.setEnabled(False)

        self.adjust_frame = QGroupBox(self.content)
        self.adjust_frame.setTitle('Adjust signal')
        self.rightPannelLayout.addWidget(self.adjust_frame, 5, 1, 5, 2)
        self.adjust_frame_layout = QGridLayout()

        self.delchn_frame = QGroupBox(self.content)
        self.delchn_frame.setTitle('Delete channels')
        self.rightPannelLayout.addWidget(self.delchn_frame, 11, 1, 4, 2)
        self.delchn_frame_layout = QGridLayout()

        self.filter_frame = QGroupBox(self.content)
        self.filter_frame.setTitle('Filter')
        self.rightPannelLayout.addWidget(self.filter_frame, 16, 1, 2, 2)
        self.filter_frame_layout = QGridLayout()

        self.compu_frame = QGroupBox(self.content)
        self.compu_frame.setTitle('Computation')
        self.rightPannelLayout.addWidget(self.compu_frame, 20, 1, 4, 2)
        self.compu_frame_layout = QGridLayout()

        # win up down
        self.dis_down = QPushButton('win down', self.content)
        self.dis_down.setToolTip('roll window down')
        self.adjust_frame_layout.addWidget(self.dis_down, 1, 1)
        self.dis_down.clicked.connect(self.disp_win_down_func)  # change value & one common display func
        self.dis_down.setEnabled(False)

        self.dis_up = QPushButton('win up', self.content)
        self.dis_up.setToolTip('roll window up')
        self.adjust_frame_layout.addWidget(self.dis_up, 1, 2)
        self.dis_up.clicked.connect(self.disp_win_up_func)  # change value & one common display func
        self.dis_up.setEnabled(False)
        # channels num
        self.dis_more_chans = QPushButton('chans+', self.content)
        self.dis_more_chans.setToolTip('more channels')
        self.adjust_frame_layout.addWidget(self.dis_more_chans, 2, 1)
        self.dis_more_chans.clicked.connect(self.disp_more_chans_func)  # change value & one common display func
        self.dis_more_chans.setEnabled(False)

        self.dis_less_chans = QPushButton('chans-', self.content)
        self.dis_less_chans.setToolTip('less channels')
        self.adjust_frame_layout.addWidget(self.dis_less_chans, 2, 2)
        self.dis_less_chans.clicked.connect(self.disp_less_chans_func)  # change value & one common display func
        self.dis_less_chans.setEnabled(False)
        # wave mag
        self.dis_add_mag = QPushButton('wave+', self.content)
        self.dis_add_mag.setToolTip('wave magnitude up')
        self.adjust_frame_layout.addWidget(self.dis_add_mag, 3, 1)
        self.dis_add_mag.clicked.connect(self.disp_add_mag_func)  # change value & one common display func
        self.dis_add_mag.setEnabled(False)

        self.dis_drop_mag = QPushButton('wave-', self.content)
        self.dis_drop_mag.setToolTip('wave magnitude down')
        self.adjust_frame_layout.addWidget(self.dis_drop_mag, 3, 2)
        self.dis_drop_mag.clicked.connect(self.disp_drop_mag_func)  # change value & one common display func
        self.dis_drop_mag.setEnabled(False)
        # win left right
        self.dis_left = QPushButton('left', self.content)
        self.dis_left.setToolTip('roll window left')
        self.adjust_frame_layout.addWidget(self.dis_left, 4, 1)
        self.dis_left.clicked.connect(self.disp_win_left_func)  # change value & one common display func
        self.dis_left.setEnabled(False)

        self.dis_right = QPushButton('right', self.content)
        self.dis_right.setToolTip('roll window right')
        self.adjust_frame_layout.addWidget(self.dis_right, 4, 2)
        self.dis_right.clicked.connect(self.disp_win_right_func)  # change value & one common display func
        self.dis_right.setEnabled(False)
        # time scale
        self.dis_shrink_time = QPushButton('shrink', self.content)
        self.dis_shrink_time.setToolTip('shrink time scale')
        self.adjust_frame_layout.addWidget(self.dis_shrink_time, 5, 1)
        self.dis_shrink_time.clicked.connect(self.disp_shrink_time_func)  # change value & one common display func
        self.dis_shrink_time.setEnabled(False)

        self.dis_expand_time = QPushButton('expand', self.content)
        self.dis_expand_time.setToolTip('expand time scale')
        self.adjust_frame_layout.addWidget(self.dis_expand_time, 5, 2)
        self.dis_expand_time.clicked.connect(self.disp_expand_time_func)  # change value & one common display func
        self.dis_expand_time.setEnabled(False)

        # filter data
        self.disp_filter_low = QLineEdit(self.content)
        self.disp_filter_low.setText('60')
        self.disp_filter_low.setToolTip('filter low boundary')
        self.filter_frame_layout.addWidget(self.disp_filter_low, 1, 1)

        self.disp_filter_high = QLineEdit(self.content)
        self.disp_filter_high.setText('140')
        self.disp_filter_high.setToolTip('filter high boudary')
        self.filter_frame_layout.addWidget(self.disp_filter_high, 1, 2)

        self.filter_button = QPushButton('bandpass filter', self.content)
        self.filter_button.setToolTip('filter the data')
        self.filter_frame_layout.addWidget(self.filter_button, 2, 1, 1, 2)
        self.filter_button.clicked.connect(self.filter_data)
        self.filter_button.setEnabled(False)

        # del channels
        self.chans_list = QListWidget(self.content)
        self.chans_list.setToolTip('choose chans to delete')
        self.chans_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.chans_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.delchn_frame_layout.addWidget(self.chans_list, 1, 1, 3, 2)

        self.chans_del_button = QPushButton(self.content)
        self.chans_del_button.setText('delete chans')
        self.chans_del_button.setToolTip('delete channels')
        self.delchn_frame_layout.addWidget(self.chans_del_button, 4, 1, 1, 2)
        self.chans_del_button.clicked.connect(self.delete_chans)
        self.chans_del_button.setEnabled(False)

        # reset data
        self.reset_data_display = QPushButton('reset data', self.content)
        self.reset_data_display.setToolTip('reset data')
        self.rightPannelLayout.addWidget(self.reset_data_display, 18, 1, 1, 2)
        self.reset_data_display.clicked.connect(self.reset_data_display_func)
        self.reset_data_display.setEnabled(False)

        # baseline time and target time selection
        self.baseline_button = QPushButton(self.content)
        self.baseline_button.setText('baseline')
        self.baseline_button.setToolTip('choose baseline time')
        self.compu_frame_layout.addWidget(self.baseline_button, 1, 1, 1, 1)
        self.baseline_button.clicked.connect(self.choose_baseline)
        self.baseline_button.setEnabled(False)

        self.target_button = QPushButton(self.content)
        self.target_button.setText('target')
        self.target_button.setToolTip('choose target time')
        self.compu_frame_layout.addWidget(self.target_button, 1, 2, 1, 1)
        self.target_button.clicked.connect(self.choose_target)
        self.target_button.setEnabled(False)
        # ei
        self.ei_button = QPushButton(self.content)
        self.ei_button.setText('ei')
        self.ei_button.setToolTip('compute epilepsy index')
        self.compu_frame_layout.addWidget(self.ei_button, 2, 1, 1, 2)
        self.ei_button.clicked.connect(self.ei_computation_func)
        self.ei_button.setEnabled(False)

        # hfer
        self.hfer_button = QPushButton(self.content)
        self.hfer_button.setText('hfer')
        self.hfer_button.setToolTip('compute high frequency energy ratio')
        self.compu_frame_layout.addWidget(self.hfer_button, 3, 1, 1, 2)
        self.hfer_button.clicked.connect(self.hfer_computation_func)
        self.hfer_button.setEnabled(False)

        # fullband
        self.fullband_button = QPushButton(self.content)
        self.fullband_button.setText('full band')
        self.fullband_button.setToolTip('compute full band characteristic')
        self.compu_frame_layout.addWidget(self.fullband_button, 4, 1, 1, 2)
        self.fullband_button.clicked.connect(self.fullband_computation_func)
        self.fullband_button.setEnabled(False)

        self.adjust_frame.setLayout(self.adjust_frame_layout)
        self.filter_frame.setLayout(self.filter_frame_layout)
        self.delchn_frame.setLayout(self.delchn_frame_layout)
        self.compu_frame.setLayout(self.compu_frame_layout)
        self.setLayout(outer_layout)

        # the whole content area starts disabled -- client_ictal.py's set_subject
        # re-enables it once a subject is selected in the Patients panel
        self.content.setEnabled(False)
