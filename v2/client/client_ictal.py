#! /usr/bin/python3
# -- coding: utf-8 -- **
"""Ictal tab -- ported from BrainQuake/client_ictal.py.

The trace viewer (load/filter/scroll edf, click-to-select baseline/target windows)
is 100% unchanged -- it's already local-only matplotlib code with no compute
dependency. What changed is the "Compute EI" button: instead of calling
compute_hfer/compute_ei_index in-process, it uploads the edf (once, on import) and
POSTs the baseline/target selection + band filter to `POST .../ictal/{edf}/ei`,
then polls the job and fetches the per-channel result from `GET .../ei-result`.

Two buttons have no v2 server-side equivalent yet and just explain that instead of
silently doing nothing:
  - "Compute HFER" plots the full time-resolved channels x time HFER heatmap, but
    the server only returns the per-channel scalar EI/HFER/onset-rank summary
    (ei-result) -- there's no endpoint that returns the full norm_target matrix.
  - "Full band" (compute_full_band: spectrogram -> PCA -> k-means) was ported to
    v2/server/app/services/ictal.py but isn't wired to any router endpoint --
    PLAN.md's Phase (b)/(d) checklists never called for one.
Both would be reasonable follow-ups if the client actually needs them.
"""
import sys
import os
import shutil
import logging

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

import numpy as np
from scipy.signal import spectrogram, butter, filtfilt
from scipy.ndimage import gaussian_filter
from scipy.signal import iirnotch

import mne
from gui_forms.ictal_form import Ictal_gui

from api_client import ApiError
import local_store

logger = logging.getLogger(__name__)


class EiComputeThread(QThread):
    done = QtCore.pyqtSignal(dict)
    failed = QtCore.pyqtSignal(str)

    def __init__(self, api, subject_id, edf_artifact_id, baseline_start, baseline_end,
                 target_start, target_end, band_low, band_high):
        super(EiComputeThread, self).__init__()
        self.api = api
        self.subject_id = subject_id
        self.edf_artifact_id = edf_artifact_id
        self.baseline_start = baseline_start
        self.baseline_end = baseline_end
        self.target_start = target_start
        self.target_end = target_end
        self.band_low = band_low
        self.band_high = band_high

    def run(self):
        try:
            job = self.api.compute_ei(self.subject_id, self.edf_artifact_id,
                                       self.baseline_start, self.baseline_end,
                                       self.target_start, self.target_end,
                                       self.band_low, self.band_high)
            final = self.api.wait_for_job(job['id'], poll_interval=1.0)
            if final['state'] != 'finished':
                self.failed.emit(final.get('progress_message') or f"job {final['state']}")
                return
            result = self.api.get_ei_result(self.subject_id, self.edf_artifact_id)
            self.done.emit(result)
        except (ApiError, Exception) as e:
            self.failed.emit(str(e))


# main class
class IctalModule(QWidget, Ictal_gui):
    def __init__(self, api, subject):
        super(IctalModule, self).__init__()
        self.setupUi(self)
        self.api = api
        self.subject = subject
        self.subject_dir = local_store.subject_dir(subject['name'])
        self.lineedit_subject_dir.setText(self.subject_dir)
        self.lineedit_patient_name.setText(subject['name'])
        self.button_inputedf.setEnabled(True)
        self.edf_artifact_id = None

        self.ei_thread = None

    def center(self):
        qr = self.frameGeometry()
        cp = QDesktopWidget().availableGeometry().center()
        qr.moveCenter(cp)
        self.move(qr.topLeft())

    def dialog_subject_dir(self):
        # The subject's local cache dir is fixed (local_store.subject_dir) now that
        # subjects are server-managed rows rather than an arbitrary folder the user
        # picks -- nothing to do here besides re-showing where it is.
        QMessageBox.information(self, '', f'Using local cache dir:\n{self.subject_dir}')

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

        self.patient_name = self.lineedit_patient_name.text()
        self.edf_data = mne.io.read_raw_edf(self.mat_filename, preload=True, stim_channel=None)
        self.preprocess_xw()
        self.band_low = 1.0
        self.band_high = 500
        self.edf_time_max = self.modified_edf_data.shape[1] / self.fs
        self.disp_flag = 0
        self.data_fomat = 1  # edf

        QMessageBox.information(self, '', 'data loaded')
        self.init_display_params()
        self.disp_refresh()

        self.reset_data_display.setEnabled(True)
        self.target_button.setEnabled(True)
        self.baseline_button.setEnabled(True)
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

    def init_display_params(self):
        self.disp_chans_num = 20
        self.disp_chans_start = 0
        self.disp_wave_mul = 10
        self.disp_time_win = 5
        self.disp_time_start = 0

        self.baseline_pos = np.array([0.0, 1.0])
        self.target_pos = np.array([0.0, self.edf_time_max])
        self.baseline_mouse = 0
        self.target_mouse = 0
        self.ei_target_start = self.target_pos[0]
        self.ei_target_end = self.target_pos[1]
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
        self.disp_flag = 0

    def disp_refresh(self):
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
        self.canvas.axes.set_ylim(self.edf_dmin, (self.disp_chans_num - 1) * self.dr + self.edf_dmax)
        self.canvas.axes.set_xlabel('time(s)')
        if hasattr(self, 'baseline_count') and self.baseline_count == 1 and (self.baseline_pos[0] > segs[0][0, 0] and self.baseline_pos[0] < segs[0][-1, 0]):
            self.canvas.axes.axvline(self.baseline_pos[0])
        if hasattr(self, 'target_count') and self.target_count == 1 and (self.target_pos[0] > segs[0][0, 0] and self.target_pos[0] < segs[0][-1, 0]):
            self.canvas.axes.axvline(self.target_pos[0])
        self.canvas.draw()

    def preprocess_xw(self):
        self.fs = self.edf_data.info['sfreq']
        self.disp_ch_names = self.edf_data.ch_names
        self.chans_list.addItems(self.disp_ch_names)
        self.origin_data, self.times = self.edf_data[:]
        self.modified_edf_data = self.origin_data.copy()
        self.origin_chans = self.disp_ch_names.copy()

    def reset_data_display_func(self):
        self.target_pos = np.array([0.0, self.edf_time_max])
        self.baseline_pos = np.array([0.0, 1.0])
        self.init_display_params()
        self.disp_refresh()
        self.ei_button.setEnabled(False)
        self.hfer_button.setEnabled(False)
        self.fullband_button.setEnabled(False)

    def origin_data_display_func(self):
        self.disp_flag = 0
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
        if e.button == 'up':
            self.disp_win_left_func()
        elif e.button == 'down':
            self.disp_win_right_func()

    def filter_data(self):
        self.modified_edf_data = self.modified_edf_data - np.mean(self.modified_edf_data, axis=0)
        notch_freqs = np.arange(50, 151, 50)
        for nf in notch_freqs:
            tb, ta = iirnotch(nf / (self.fs / 2), 30)
            self.modified_edf_data = filtfilt(tb, ta, self.modified_edf_data, axis=-1)
        self.band_low = float(self.disp_filter_low.text())
        self.band_high = float(self.disp_filter_high.text())
        nyq = self.fs / 2
        b, a = butter(5, np.array([self.band_low / nyq, self.band_high / nyq]), btype='bandpass')
        self.modified_edf_data = filtfilt(b, a, self.modified_edf_data)
        self.disp_flag = 1
        self.disp_refresh()
        self.ei_button.setEnabled(True)
        self.hfer_button.setEnabled(True)

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

    def choose_baseline(self):
        self.baseline_mouse = 1
        self.baseline_count = 0

    def choose_target(self):
        self.target_mouse = 1
        self.target_count = 0

    def canvas_press_button(self, e):
        if hasattr(self, 'baseline_mouse') and self.baseline_mouse == 1:
            self.baseline_pos[self.baseline_count] = e.xdata
            self.canvas.axes.axvline(e.xdata)
            self.canvas.draw()
            self.baseline_count += 1
            if self.baseline_count == 2:
                self.baseline_mouse = 0
                reply = QMessageBox.question(self, 'confirm', 'confirm baseline?', QMessageBox.Yes | QMessageBox.No,
                                             QMessageBox.Yes)
                if reply == QMessageBox.Yes:
                    pass
                else:
                    self.baseline_pos = np.array([0.0, 1.0])
                    self.disp_refresh()
        elif hasattr(self, 'target_mouse') and self.target_mouse == 1:
            self.target_pos[self.target_count] = e.xdata
            self.canvas.axes.axvline(e.xdata)
            self.canvas.draw()
            self.target_count += 1
            if self.target_count == 2:
                self.target_mouse = 0
                reply = QMessageBox.question(self, 'confim', 'confirm target time?', QMessageBox.Yes | QMessageBox.No,
                                             QMessageBox.Yes)
                if reply == QMessageBox.Yes:
                    self.disp_time_start = self.target_pos[0]
                    self.disp_time_win = self.target_pos[1] - self.target_pos[0]
                    self.disp_refresh()
                else:
                    self.target_pos = np.array([0.0, self.edf_time_max])
                    self.disp_refresh()
                    self.canvas.axes.axvline(self.baseline_pos[0])
                    self.canvas.axes.axvline(self.baseline_pos[1])
                    self.canvas.draw()
        else:
            pass

    # ei computation -- now a REST job instead of a local compute_hfer/compute_ei_index call
    def ei_computation_func(self):
        if not self.edf_artifact_id:
            QMessageBox.warning(self, '', 'Import an edf file first.')
            return
        self.ei_button.setEnabled(False)
        QMessageBox.information(self, '', 'EI computation starting, please wait')
        self.ei_thread = EiComputeThread(
            self.api, self.subject['id'], self.edf_artifact_id,
            float(self.baseline_pos[0]), float(self.baseline_pos[1]),
            float(self.target_pos[0]), float(self.target_pos[1]),
            self.band_low, self.band_high)
        self.ei_thread.done.connect(self._ei_computation_done)
        self.ei_thread.failed.connect(self._ei_computation_failed)
        self.ei_thread.start()

    def _ei_computation_failed(self, msg):
        self.ei_button.setEnabled(True)
        QMessageBox.critical(self, '', f'EI computation failed:\n{msg}')

    def _ei_computation_done(self, result):
        self.ei_button.setEnabled(True)
        self.ei_ei = np.array(result['ei'])
        self.ei_hfer = np.array(result['hfer'])
        self.ei_onset_rank = np.array(result['onset_rank'])
        logger.info('finish ei computation')

        # for click-display signals (unchanged -- still needs the local raw signal)
        self.tmp_origin_edf_data = self.origin_data.copy()
        remain_chInd = np.array([x in self.disp_ch_names for x in self.origin_chans])
        self.tmp_origin_remainData = self.tmp_origin_edf_data[remain_chInd]
        self.tmp_origin_remainData = self.tmp_origin_remainData - np.mean(self.tmp_origin_remainData, axis=0)
        notch_freqs = np.arange(50, 151, 50)
        for nf in notch_freqs:
            tb, ta = iirnotch(nf / (self.fs / 2), 30)
            self.tmp_origin_remainData = filtfilt(tb, ta, self.tmp_origin_remainData, axis=-1)

        self.ei_target_start = int(self.target_pos[0] * self.fs)
        self.ei_target_end = int(self.target_pos[1] * self.fs)
        self.ei_plot_xw_func()

    # hfer computation -- not available: the server only returns the per-channel
    # scalar EI/HFER/onset-rank summary, not the full time-resolved matrix this
    # heatmap needs (see module docstring)
    def hfer_computation_func(self):
        QMessageBox.information(
            self, '',
            "The full HFER heatmap isn't available via REST yet -- the server's "
            "ei-result only returns the per-channel scalar summary used for the EI "
            "bar chart, not the full channels x time matrix this view needs.")

    def ei_plot_xw_func(self):
        ei_mu = np.mean(self.ei_ei)
        ei_std = np.std(self.ei_ei)
        self.ei_thresh = ei_mu + ei_std

        self.ei_ei_fig = plt.figure('ei')
        ei_ei_ax = self.ei_ei_fig.add_subplot(111)
        ei_hfer_fig = plt.figure('hfer')
        ei_hfer_ax = ei_hfer_fig.add_subplot(111)
        ei_onset_rank_fig = plt.figure('onset')
        ei_onset_rank_ax = ei_onset_rank_fig.add_subplot(111)
        ei_data = np.stack([self.ei_hfer, self.ei_onset_rank], axis=0)
        title_data = ['High frequency Energy Coefficient', 'Time Coefficient']
        ei_axes = [ei_hfer_ax, ei_onset_rank_ax]

        ei_ei_ax.bar(range(len(self.ei_ei)), self.ei_ei)
        ei_ei_ax.set_title('High Frequency Epileptogenicity Index')
        ei_ind = list(np.squeeze(np.where(self.ei_ei > self.ei_thresh)))
        for ind in ei_ind:
            ei_ei_ax.text(ind - 0.8, self.ei_ei[ind] + 0.01, self.disp_ch_names[ind], fontsize=8, color='k')
        ei_ei_ax.plot(np.arange(len(self.ei_ei)), self.ei_thresh * np.ones(len(self.ei_ei)), 'r--')
        for i in range(len(ei_data)):
            ei_axes[i].bar(range(len(ei_data[i])), ei_data[i])
            ei_axes[i].set_title(title_data[i])
        self.ei_ei_fig.canvas.mpl_connect('button_press_event', self.ei_press_func)
        plt.show()

    def ei_press_func(self, e):
        if e.button == 1:
            chosen_elec_index = int(round(e.xdata))
            elec_name = self.disp_ch_names[chosen_elec_index]
            raw_data_indx = self.disp_ch_names.index(elec_name)
            tmp_origin_edf_data = self.tmp_origin_remainData
            tmp_data = tmp_origin_edf_data[raw_data_indx, self.ei_target_start:self.ei_target_end]
            tmp_time_target = np.linspace(self.ei_target_start / self.fs, self.ei_target_end / self.fs,
                                          int((self.ei_target_end - self.ei_target_start)))

            fig = plt.figure('signal')
            ax1 = fig.add_axes([0.2, 0.6, 0.6, 0.3])
            ax1.cla()
            ax1.set_title(elec_name + ' signal')
            if self.data_fomat == 1:
                tmp_data_plot = tmp_data * 1000
            elif self.data_fomat == 0:
                tmp_data_plot = tmp_data / 1000
            ax1.plot(tmp_time_target, tmp_data_plot)
            ax1.set_xlabel('time(s)')
            ax1.set_ylabel('signal(mV)')
            ax1.set_xlim(tmp_time_target[0], tmp_time_target[-1])
            ax1_ymax = np.abs(tmp_data_plot).max()
            ax1.set_ylim([-ax1_ymax, ax1_ymax])
            ax2 = fig.add_axes([0.2, 0.15, 0.6, 0.3])
            ax2.cla()
            ax2.set_title(elec_name + ' spectrogram')
            f, t, sxx = spectrogram(x=tmp_data, fs=int(self.fs), nperseg=int(0.5 * self.fs),
                                    noverlap=int(0.9 * 0.5 * self.fs), nfft=1024, mode='magnitude')
            sxx = (sxx - np.mean(sxx, axis=1, keepdims=True)) / np.std(sxx, axis=1, keepdims=True)
            sxx = gaussian_filter(sxx, sigma=2)
            spec_time = np.linspace(t[0] + tmp_time_target[0], t[-1] + tmp_time_target[0], sxx.shape[1])
            spec_f_max = 300
            spec_f_nums = int(len(f) * spec_f_max / f.max())
            spec_f = np.linspace(0, spec_f_max, spec_f_nums)
            spec_sxx = sxx[:spec_f_nums, :]

            spec_time, spec_f = np.meshgrid(spec_time, spec_f)
            surf = ax2.pcolormesh(spec_time, spec_f, spec_sxx, cmap=plt.cm.hot, vmax=2, vmin=-0.8, shading='auto')

            ax2.set_xlabel('time(s)')
            ax2.set_ylabel('frequency(hz)')
            ax2.set_ylim((0, spec_f_max))
            ax2.set_xlim(tmp_time_target[0], tmp_time_target[-1])
            position = fig.add_axes([0.85, 0.15, 0.02, 0.3])
            cb = plt.colorbar(surf, cax=position)
            plt.show()
        elif e.button == 3:
            self.ei_thresh = e.ydata
            self.ei_ei_fig.clf()
            ei_ei_ax = self.ei_ei_fig.add_axes([0.1, 0.1, 0.75, 0.8])
            ei_ei_ax.bar(range(len(self.ei_ei)), self.ei_ei)
            ei_ei_ax.set_title('High Frequency Epileptogenicity Index')
            ei_ind = list(np.squeeze(np.where(self.ei_ei > self.ei_thresh)))
            for ind in ei_ind:
                ei_ei_ax.text(ind - 0.8, self.ei_ei[ind] + 0.01, self.disp_ch_names[ind], fontsize=8, color='k')
            ei_ei_ax.plot(np.arange(len(self.ei_ei)), self.ei_thresh * np.ones(len(self.ei_ei)), 'r--')
            plt.show()

    # full band computation -- not wired to any v2 server endpoint (see module docstring)
    def fullband_computation_func(self):
        QMessageBox.information(
            self, '',
            "Full-band spectral clustering wasn't wired to a v2 server endpoint -- "
            "services/ictal.py's compute_full_band() exists but PLAN.md's Phase (b)/(d) "
            "checklists never called for a router endpoint for it.")


if __name__ == '__main__':
    from api_client import ApiClient
    app = QApplication(sys.argv)
    api = ApiClient()
    subjects = api.list_subjects()
    if not subjects:
        print("No subjects on the server -- create one first (e.g. via client_main.py).")
        sys.exit(1)
    win = IctalModule(api, subjects[0])
    win.show()
    sys.exit(app.exec_())
