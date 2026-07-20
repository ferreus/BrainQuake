#! /usr/bin/python3
# encoding=utf-8
"""Interictal tab -- ported from BrainQuake/client_inter.py.

Same pattern as client_ictal.py: the trace viewer stays 100% local; the "HI
computation" button now POSTs to `.../interictal/{edf}/hfo` and polls instead of
calling HI_preprocess_file/HI_count_highEvents_chns in-process. The detected-event
overlay (disp_refresh_HFOdets_filt/raw) used to re-read the *_events.npz file off
disk on every redraw; now it just reuses the REST result already fetched into
self.hfoDets_chns/self.hfoDets_times by HI_plot_func.
"""
import sys
import logging

from PyQt5.QtWidgets import QApplication, QSizePolicy, QMessageBox, QWidget, \
    QPushButton, QLineEdit, QGridLayout, QFileDialog, QListWidget, QLabel, QFrame, QGroupBox, QProgressBar
from PyQt5.QtCore import Qt, QThread
import PyQt5.QtWidgets as QtWidgets
import PyQt5.QtCore as QtCore

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib import cm

import numpy as np
from scipy.signal import butter, filtfilt, iirnotch

import mne
import os
import shutil

from gui_forms.inter_form import Interictal_gui
from api_client import ApiError
import local_store

logger = logging.getLogger(__name__)


class HiComputeThread(QThread):
    done = QtCore.pyqtSignal(dict)
    failed = QtCore.pyqtSignal(str)
    progress = QtCore.pyqtSignal(int)

    def __init__(self, api, subject_id, edf_artifact_id, relThr, absThr, minGap, minDur, freqband, chns_list):
        super(HiComputeThread, self).__init__()
        self.api = api
        self.subject_id = subject_id
        self.edf_artifact_id = edf_artifact_id
        self.relThr = relThr
        self.absThr = absThr
        self.minGap = minGap
        self.minDur = minDur
        self.freqband = freqband
        self.chns_list = chns_list

    def run(self):
        try:
            job = self.api.compute_hfo(
                self.subject_id, self.edf_artifact_id,
                band_low=self.freqband[0], band_high=self.freqband[1],
                rel_thresh=self.relThr, abs_thresh=self.absThr,
                min_gap=self.minGap, min_last=self.minDur, remain_chns=self.chns_list)

            def on_progress(j):
                self.progress.emit(int(j.get('progress_pct', 0)))

            final = self.api.wait_for_job(job['id'], poll_interval=1.0, on_progress=on_progress)
            if final['state'] != 'finished':
                self.failed.emit(final.get('progress_message') or f"job {final['state']}")
                return
            result = self.api.get_hfo_result(self.subject_id, self.edf_artifact_id)
            self.progress.emit(100)
            self.done.emit(result)
        except (ApiError, Exception) as e:
            self.failed.emit(str(e))


# main class
class InterModule(QWidget, Interictal_gui):
    def __init__(self, api, subject=None):
        super(InterModule, self).__init__()
        self.setupUi(self)
        self.api = api
        self.subject = None
        self.subject_dir = None
        self.edf_artifact_id = None
        self.hi_thread = None
        self.hfoDets_chns = None
        self.hfoDets_times = None
        # guards disp_scroll_mouse against firing before any edf has been loaded --
        # matplotlib events aren't gated by Qt's widget-enabled state (see the
        # client_ictal.py fix for the same class of bug)
        self._edf_loaded = False
        self._set_status(subject is not None)
        if subject:
            self.set_subject(subject)

    def _set_status(self, has_subject):
        self.content.setEnabled(has_subject)
        if has_subject:
            self.status_label.setText(f"Ready -- {self.subject['name']}. Import an .edf file to begin.")
        else:
            self.status_label.setText('Select a patient in the Patients panel to begin.')

    def set_subject(self, subject):
        """Called by main_window.py when the Patients panel selection changes (and
        by __init__ if constructed with one already). Same reasoning as
        client_ictal.py's IctalModule.set_subject: the trace-viewer/edf state is
        tied to one subject's uploaded edf, so switching subjects resets the tab."""
        if subject is None:
            self.subject = None
            self._edf_loaded = False
            self.button_inputedf.setEnabled(False)
            self._set_status(False)
            return
        if self.subject and subject['id'] == self.subject['id']:
            return
        self.subject = subject
        self.subject_dir = local_store.subject_dir(subject['name'])
        self.edf_artifact_id = None
        self.hfoDets_chns = None
        self.hfoDets_times = None
        self._edf_loaded = False
        self._set_status(True)
        self.button_inputedf.setEnabled(True)
        self.canvas.axes.cla()
        self.canvas.draw()
        for btn in (self.reset_data_display, self.chans_del_button, self.filter_button,
                    self.dis_up, self.dis_down, self.dis_add_mag, self.dis_drop_mag,
                    self.dis_more_chans, self.dis_less_chans, self.dis_shrink_time,
                    self.dis_expand_time, self.dis_left, self.dis_right, self.HI_button,
                    self.hiDetsFilt_button, self.hiDetsRaw_button):
            btn.setEnabled(False)

    def dialog_inputedfdata(self):
        picked_filename, b = QFileDialog.getOpenFileName(self, 'open edf file', './', '(*.edf)')
        if not picked_filename:
            return
        edf_dir = local_store.edf_dir(self.subject['name'])
        dest_path = os.path.join(edf_dir, os.path.basename(picked_filename))
        if os.path.abspath(picked_filename) != os.path.abspath(dest_path):
            shutil.copy2(picked_filename, dest_path)
        self.mat_filename = dest_path

        try:
            artifact = self.api.upload_file(self.subject['id'], 'edf', dest_path)
        except (ApiError, Exception) as e:
            QMessageBox.critical(self, '', f'Failed to upload edf to server:\n{e}')
            return
        self.edf_artifact_id = artifact['id']

        self.patient_name = self.subject['name']
        self.edf_data = mne.io.read_raw_edf(self.mat_filename, preload=True, stim_channel=None)
        self.preprocess_xw()
        self.band_low = 1.0
        self.band_high = self.fs / 2 - 1
        self.edf_time_max = self.modified_edf_data.shape[1] / self.fs

        self.init_display_params()
        self.disp_refresh()

        self.reset_data_display.setEnabled(True)
        self.chans_del_button.setEnabled(True)
        self.filter_button.setEnabled(True)
        self.dis_up.setEnabled(True)
        self.dis_down.setEnabled(True)
        self.dis_add_mag.setEnabled(True)
        self.dis_drop_mag.setEnabled(True)
        self.dis_more_chans.setEnabled(True)
        self.dis_less_chans.setEnabled(True)
        self.dis_shrink_time.setEnabled(True)
        self.dis_expand_time.setEnabled(True)
        self.dis_left.setEnabled(True)
        self.dis_right.setEnabled(True)
        self.HI_button.setEnabled(True)
        self._edf_loaded = True

    def preprocess_xw(self):
        self.fs = self.edf_data.info['sfreq']
        self.disp_ch_names = self.edf_data.ch_names
        self.chans_list.addItems(self.disp_ch_names)
        self.origin_data, self.times = self.edf_data[:]
        self.modified_edf_data = self.origin_data.copy()
        self.origin_chans = self.disp_ch_names.copy()

    def init_display_params(self):
        self.disp_chans_num = 20
        self.disp_chans_start = 0
        self.disp_wave_mul = 10
        self.disp_time_win = 5
        self.disp_time_start = 0

        self.modified_edf_data = self.origin_data.copy()
        self.disp_ch_names = self.origin_chans.copy()
        self.chans_list.clear()
        self.chans_list.addItems(self.disp_ch_names)

        self.edf_time = self.modified_edf_data.shape[1] / self.fs
        self.edf_nchans = len(self.chans_list)
        self.edf_line_colors = np.array([cm.jet(x) for x in np.random.rand(self.edf_nchans)])
        self.edf_dmin = self.modified_edf_data[:, :].min()
        self.edf_dmax = self.modified_edf_data[:, :].max()
        self.disp_press = 0.7
        self.dr = (self.edf_dmax - self.edf_dmin) * self.disp_press
        self.y0 = self.edf_dmin
        self.y1 = (self.disp_chans_num - 1) * self.dr + self.edf_dmax

    def disp_refresh_ori(self):
        self.canvas.axes.cla()
        self.canvas.axes.set_ylim(self.y0, self.y1)
        segs = []
        ticklocs = []
        self.disp_start = int(self.disp_time_start * self.fs)
        self.disp_end = int((self.disp_time_start + self.disp_time_win) * self.fs)
        self.disp_end = min(self.disp_end, self.modified_edf_data.shape[1])
        if self.disp_chans_num >= self.modified_edf_data.shape[0]:
            self.disp_chans_start = 0
            self.disp_chans_num = self.modified_edf_data.shape[0]
        elif self.disp_chans_start + self.disp_chans_num >= self.modified_edf_data.shape[0]:
            self.disp_chans_start = self.modified_edf_data.shape[0] - self.disp_chans_num
        for i in range(self.disp_chans_start, self.disp_chans_start + self.disp_chans_num):
            tmp_data = self.modified_edf_data[i, self.disp_start:self.disp_end]
            tmp_time = np.linspace(self.disp_start / self.fs, self.disp_end / self.fs, self.disp_end - self.disp_start)
            tmp_data = tmp_data * self.disp_wave_mul
            tickloc = (i - self.disp_chans_start) * self.dr
            segs.append(np.hstack((tmp_time[:, np.newaxis], (tmp_data + tickloc)[:, np.newaxis])))
            ticklocs.append(tickloc)
        colors = self.edf_line_colors[self.disp_chans_start:self.disp_chans_start + self.disp_chans_num]
        lines = LineCollection(segs, linewidths=0.7, colors='k')
        disp_chan_names = self.disp_ch_names[self.disp_chans_start:(self.disp_chans_start + self.disp_chans_num)]
        self.canvas.axes.set_xlim(segs[0][0, 0], segs[0][-1, 0])
        self.canvas.axes.add_collection(lines)

        self.canvas.axes.set_yticks(ticklocs)
        self.canvas.axes.set_yticklabels(disp_chan_names)
        self.canvas.axes.set_ylim(-self.dr / 2, (self.disp_chans_num - 1) * self.dr + self.dr / 2)
        self.canvas.axes.set_xlabel('time(s)')
        self.canvas.draw()

    def reset_data_display_func(self):
        self.init_display_params()
        self.disp_refresh = self.disp_refresh_ori
        self.disp_refresh()

    def disp_win_down_func(self):
        self.disp_chans_start -= self.disp_chans_num
        if self.disp_chans_start <= 0:
            self.disp_chans_start = 0
        self.disp_refresh()

    def disp_win_up_func(self):
        self.disp_chans_start += self.disp_chans_num
        if self.disp_chans_start + self.disp_chans_num >= self.modified_edf_data.shape[0]:
            self.disp_chans_start = self.modified_edf_data.shape[0] - self.disp_chans_num
        self.disp_refresh()

    def disp_more_chans_func(self):
        self.disp_chans_num *= 2
        if self.disp_chans_num >= self.modified_edf_data.shape[0]:
            self.disp_chans_start = 0
            self.disp_chans_num = self.modified_edf_data.shape[0]
        elif self.disp_chans_start + self.disp_chans_num >= self.modified_edf_data.shape[0]:
            self.disp_chans_start = self.modified_edf_data.shape[0] - self.disp_chans_num
        self.disp_refresh()

    def disp_less_chans_func(self):
        self.disp_chans_num = int(self.disp_chans_num / 2.0)
        if self.disp_chans_num <= 1:
            self.disp_chans_num = 1
        self.disp_refresh()

    def disp_add_mag_func(self):
        self.disp_wave_mul *= 1.5
        self.disp_refresh()

    def disp_drop_mag_func(self):
        self.disp_wave_mul *= 0.75
        self.disp_refresh()

    def disp_win_left_func(self):
        self.disp_time_start -= 0.2 * self.disp_time_win
        if self.disp_time_start <= 0:
            self.disp_time_start = 0
        self.disp_refresh()

    def disp_win_right_func(self):
        self.disp_time_start += 0.2 * self.disp_time_win
        if self.disp_time_start + self.disp_time_win >= self.edf_time:
            self.disp_time_start = self.edf_time - self.disp_time_win
        self.disp_refresh()

    def disp_shrink_time_func(self):
        self.disp_time_win += 2
        if self.disp_time_win >= self.edf_time:
            self.disp_time_win = self.edf_time
        self.disp_refresh()

    def disp_expand_time_func(self):
        self.disp_time_win -= 2
        if self.disp_time_win <= 2:
            self.disp_time_win = 2
        self.disp_refresh()

    def disp_scroll_mouse(self, e):
        # matplotlib's scroll_event isn't gated by Qt's widget-enabled state, so this
        # can fire from a scroll over the canvas before any edf has loaded -- see the
        # same fix in client_ictal.py's disp_scroll_mouse
        if not self._edf_loaded:
            return
        if e.button == 'up':
            self.disp_win_left_func()
        elif e.button == 'down':
            self.disp_win_right_func()

    def filter_data(self):
        self.modified_edf_data = self.modified_edf_data - np.mean(self.modified_edf_data, axis=0)
        self.band_low = float(self.disp_filter_low.text())
        self.band_high = float(self.disp_filter_high.text())
        notch_freqs = np.arange(50, self.band_high + 10, 50)
        for nf in notch_freqs:
            tb, ta = iirnotch(nf / (self.fs / 2), 30)
            self.modified_edf_data = filtfilt(tb, ta, self.modified_edf_data, axis=-1)
        nyq = self.fs / 2
        b, a = butter(5, np.array([self.band_low / nyq, self.band_high / nyq]), btype='bandpass')
        self.modified_edf_data = filtfilt(b, a, self.modified_edf_data)
        self.disp_wave_mul = self.dr / (self.modified_edf_data.std() * 10)
        self.disp_refresh()

    def delete_chans(self):
        deleted_chans = self.chans_list.selectedItems()
        deleted_list = [i.text() for i in deleted_chans]
        deleted_ind_list = []
        for deleted_name in deleted_list:
            deleted_ind_list.append(self.disp_ch_names.index(deleted_name))
        new_modified_data = np.delete(self.modified_edf_data, deleted_ind_list, axis=0)
        self.modified_edf_data = new_modified_data
        for d_chan in deleted_list:
            self.disp_ch_names.remove(d_chan)
        self.chans_list.clear()
        self.chans_list.addItems(self.disp_ch_names)
        self.disp_refresh()

    def get_HI_compu_params(self):
        self.relThr_val = float(self.lineedit_relTh_name.text())
        self.absThr_val = float(self.lineedit_absTh_name.text())
        self.minGap_val = float(self.lineedit_minGap_name.text())
        self.minDur_val = float(self.lineedit_minDur_name.text())
        self.chnList_val = []
        remain_chns_count = self.chans_list.count()
        for ci in range(remain_chns_count):
            self.chnList_val.append(self.chans_list.item(ci).text())

    def HI_computation_func(self):
        if not self.edf_artifact_id:
            QMessageBox.information(self, '', 'please select an interictal edf file')
            return
        self.HI_button.setEnabled(False)
        self.get_HI_compu_params()
        QMessageBox.information(self, '', 'High frequency events Index computation starting, please wait')
        self.HI_proBar.setValue(0)
        self.hi_thread = HiComputeThread(
            self.api, self.subject['id'], self.edf_artifact_id,
            relThr=self.relThr_val, absThr=self.absThr_val, minGap=self.minGap_val, minDur=self.minDur_val,
            freqband=[self.band_low, self.band_high], chns_list=self.chnList_val)
        self.hi_thread.progress.connect(self.HI_proBar.setValue)
        self.hi_thread.done.connect(self.HI_plot_func)
        self.hi_thread.failed.connect(self._HI_computation_failed)
        self.hi_thread.start()

    def _HI_computation_failed(self, msg):
        self.HI_button.setEnabled(True)
        QMessageBox.critical(self, '', f'HI computation failed:\n{msg}')

    def HI_plot_func(self, result):
        QMessageBox.information(self, '', 'HI computation done')
        self.HI_button.setEnabled(True)
        self.hiDetsFilt_button.clicked.connect(self.reset_refresh_filt)
        self.hiDetsFilt_button.setEnabled(True)
        self.hiDetsRaw_button.clicked.connect(self.reset_refresh_raw)
        self.hiDetsRaw_button.setEnabled(True)

        self.HI_chnCounts = np.array(result['event_counts'])
        self.HI_chnNames = result['chn_names']
        self.hfoDets_chns = result['chn_names']
        self.hfoDets_times = result['event_times']

        HI_fig = plt.figure('interIctal HI')
        HI_ax = HI_fig.add_subplot(111)
        HI_ax.bar(np.arange(len(self.HI_chnCounts)), self.HI_chnCounts, color=(50 / 255, 168 / 255, 82 / 255))
        for chi, chName in enumerate(self.HI_chnNames):
            HI_ax.text(chi, self.HI_chnCounts[chi], chName, va='bottom', ha='center')
        plt.xlabel('Channels')
        plt.ylabel('HI')
        plt.show()

    def reset_refresh_filt(self):
        self.disp_refresh = self.disp_refresh_HFOdets_filt
        self.disp_wave_mul = self.dr / (self.modified_edf_data.std() * 10)
        self.disp_refresh()

    def reset_refresh_raw(self):
        self.disp_refresh = self.disp_refresh_HFOdets_raw
        self.disp_wave_mul = self.dr / (np.median(np.std(self.origin_data, axis=1)) * 2)
        self.disp_refresh()

    def _draw_hfo_overlay(self, disp_chan_names):
        if not self.hfoDets_chns:
            return
        showDets_index = [self.hfoDets_chns.index(x) if x in self.hfoDets_chns else [] for x in disp_chan_names]
        showDets_times = [self.hfoDets_times[x] if x != [] else [] for x in showDets_index]
        for ci in range(len(showDets_times)):
            if len(showDets_times[ci]) == 0:
                continue
            for ti, tw in enumerate(showDets_times[ci]):
                if tw[0] > (self.disp_start / self.fs) and tw[1] < (self.disp_end / self.fs):
                    self.canvas.axes.plot([tw[0], tw[1]], [self.dr * ci, self.dr * ci], 'r-', linewidth=2)

    def disp_refresh_HFOdets_filt(self):
        self.canvas.axes.cla()
        self.canvas.axes.set_ylim(self.y0, self.y1)
        segs = []
        ticklocs = []
        self.disp_start = int(self.disp_time_start * self.fs)
        self.disp_end = int((self.disp_time_start + self.disp_time_win) * self.fs)
        self.disp_end = min(self.disp_end, self.modified_edf_data.shape[1])
        if self.disp_chans_num >= self.modified_edf_data.shape[0]:
            self.disp_chans_start = 0
            self.disp_chans_num = self.modified_edf_data.shape[0]
        elif self.disp_chans_start + self.disp_chans_num >= self.modified_edf_data.shape[0]:
            self.disp_chans_start = self.modified_edf_data.shape[0] - self.disp_chans_num
        for i in range(self.disp_chans_start, self.disp_chans_start + self.disp_chans_num):
            tmp_data = self.modified_edf_data[i, self.disp_start:self.disp_end]
            tmp_time = np.linspace(self.disp_start / self.fs, self.disp_end / self.fs, self.disp_end - self.disp_start)
            tmp_data = tmp_data * self.disp_wave_mul
            tickloc = (i - self.disp_chans_start) * self.dr
            segs.append(np.hstack((tmp_time[:, np.newaxis], (tmp_data + tickloc)[:, np.newaxis])))
            ticklocs.append(tickloc)
        lines = LineCollection(segs, linewidths=0.7, colors='k')
        disp_chan_names = self.disp_ch_names[self.disp_chans_start:(self.disp_chans_start + self.disp_chans_num)]
        self.canvas.axes.set_xlim(segs[0][0, 0], segs[0][-1, 0])
        self.canvas.axes.add_collection(lines)
        self._draw_hfo_overlay(disp_chan_names)

        self.canvas.axes.set_yticks(ticklocs)
        self.canvas.axes.set_yticklabels(disp_chan_names)
        self.canvas.axes.set_ylim(-self.dr / 2, (self.disp_chans_num - 1) * self.dr + self.dr / 2)
        self.canvas.axes.set_xlabel('time(s)')
        self.canvas.draw()

    def disp_refresh_HFOdets_raw(self):
        self.canvas.axes.cla()
        self.canvas.axes.set_ylim(self.y0, self.y1)
        segs = []
        ticklocs = []
        self.disp_start = int(self.disp_time_start * self.fs)
        self.disp_end = int((self.disp_time_start + self.disp_time_win) * self.fs)
        self.modRaw_edf_dataIndex = [self.origin_chans.index(x) for x in self.disp_ch_names]
        self.modRaw_edf_data = self.origin_data[self.modRaw_edf_dataIndex]
        self.modRaw_edf_data = self.modRaw_edf_data - np.mean(self.modRaw_edf_data, axis=0, keepdims=True)
        self.disp_end = min(self.disp_end, self.modRaw_edf_data.shape[1])
        if self.disp_chans_num >= self.modRaw_edf_data.shape[0]:
            self.disp_chans_start = 0
            self.disp_chans_num = self.modRaw_edf_data.shape[0]
        elif self.disp_chans_start + self.disp_chans_num >= self.modRaw_edf_data.shape[0]:
            self.disp_chans_start = self.modRaw_edf_data.shape[0] - self.disp_chans_num
        for i in range(self.disp_chans_start, self.disp_chans_start + self.disp_chans_num):
            tmp_data = self.modRaw_edf_data[i, self.disp_start:self.disp_end]
            tmp_time = np.linspace(self.disp_start / self.fs, self.disp_end / self.fs, self.disp_end - self.disp_start)
            tmp_data = tmp_data * self.disp_wave_mul
            tickloc = (i - self.disp_chans_start) * self.dr
            segs.append(np.hstack((tmp_time[:, np.newaxis], (tmp_data + tickloc)[:, np.newaxis])))
            ticklocs.append(tickloc)
        lines = LineCollection(segs, linewidths=0.7, colors='k')
        disp_chan_names = self.disp_ch_names[self.disp_chans_start:(self.disp_chans_start + self.disp_chans_num)]
        self.canvas.axes.set_xlim(segs[0][0, 0], segs[0][-1, 0])
        self.canvas.axes.add_collection(lines)
        self._draw_hfo_overlay(disp_chan_names)

        self.canvas.axes.set_yticks(ticklocs)
        self.canvas.axes.set_yticklabels(disp_chan_names)
        self.canvas.axes.set_ylim(-self.dr / 2, (self.disp_chans_num - 1) * self.dr + self.dr / 2)
        self.canvas.axes.set_xlabel('time(s)')
        self.canvas.draw()


if __name__ == '__main__':
    from api_client import ApiClient
    app = QApplication(sys.argv)
    api = ApiClient()
    subjects = api.list_subjects()
    if not subjects:
        print("No subjects on the server -- create one first (e.g. via client_main.py).")
        sys.exit(1)
    win = InterModule(api, subjects[0])
    win.show()
    sys.exit(app.exec_())
