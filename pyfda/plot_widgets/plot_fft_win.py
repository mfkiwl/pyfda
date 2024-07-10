# -*- coding: utf-8 -*-
#
# This file is part of the pyFDA project hosted at https://github.com/chipmuenk/pyfda
#
# Copyright © pyFDA Project Contributors
# Licensed under the terms of the MIT License
# (see file LICENSE in root directory for details)

"""
Create a popup window with FFT window information
"""
import copy
import numpy as np
from numpy.fft import fft, fftshift, fftfreq
from scipy.signal import argrelmin

import matplotlib.patches as mpl_patches

from pyfda.libs.pyfda_lib import safe_eval, to_html, pprint_log
from pyfda.libs.pyfda_qt_lib import (
    qwindow_stay_on_top, qtext_width, QVLine, QHLine)
from pyfda.libs.fft_windows_cmb_box import QFFTWinSelector
from pyfda.plot_widgets.mpl_widget import MplWidget

# importing filterbroker initializes all its globals:
import pyfda.filterbroker as fb

from pyfda.libs.compat import (
    Qt, pyqtSignal, QHBoxLayout, QVBoxLayout, QDialog, QCheckBox, QLabel, QLineEdit,
    QFrame, QFont, QPushButton, QTextBrowser, QSplitter, QTableWidget, QTableWidgetItem,
    QSizePolicy, QHeaderView)
import logging
logger = logging.getLogger(__name__)


# ------------------------------------------------------------------------------
class Plot_FFT_win(QDialog):
    """
    Create a pop-up widget for displaying time and frequency view of an FFT
    window.

    Window data is taken from the global dictionary `all_wins_dict_ref` and restricted
    to the target application `app={'fir', 'spec', 'stft'}. Available windows,
    parameters, tooltipps etc are then provided by the widget
    `pyfda_fft_windows_lib.QFFTWinSelection`

    Parameters
    ----------

    parent : class instance
        reference to parent

    cur_win_dict : dict
        Dictionary keeping the current window and its parameters. This is usually
        a sub-dictionary of `fb.fil[0]`.

    app : str
        String specifying the target application, 'fir' for windowed fir filter design,
        'spec' for general spectral analysis and 'stft' for short-time fourier transform
        windowing. The argument is passed to the constructor of `QFFTWinSelector()`
        in `_construct_UI()`.

    sym : bool
        Passed to `get_window()`:
        When True, generate a symmetric window for use in filter design.
        When False (default), generate a periodic window for use in spectral analysis.

    title : str
        Title text for Qt Window

    ignore_close_event : bool
        Disable close event when True (Default)

    Methods
    -------

    - `self.calc_N()`
    - `self.update_view()`:
    - `self.draw()`: calculate window and FFT and draw both
    - `get_win(N)` : Get the window array
    """
    sig_rx = pyqtSignal(object)  # incoming
    sig_tx = pyqtSignal(object)  # outgoing
    from pyfda.libs.pyfda_qt_lib import emit

    def __init__(self, cur_win_dict: dict, app: str = 'spec', all_wins_dict: dict = {},
                 sym: bool = False, title: str = 'pyFDA Window Viewer',
                 ignore_close_event: bool = True, object_name: str = "plot_fft_win"
                 ) -> None:
        super().__init__()
        self.setObjectName(object_name)
        # make window stay on top
        qwindow_stay_on_top(self, True)

        self.cur_win_dict = cur_win_dict
        self.app = app
        self.sym = sym
        self.ignore_close_event = ignore_close_event
        self.setWindowTitle(title)

        self.needs_calc = True

        self.bottom_f = -80  # min. value for dB display
        self.bottom_t = -60
        # initial number of data points for visualization
        self.N_view = self.cur_win_dict['win_len']

        self.pad = 16  # zero padding factor for smooth FFT plot

        # initial settings for checkboxes
        self.tbl_sel = [True, True, False, False]
        self.tbl_cols = 6
        self.tbl_rows = len(self.tbl_sel) // (self.tbl_cols // 3)

        self.tooltips_tbl = [
            "<span>Normalized Equivalent Noise Bandwidth: The spectrum needs to be "
            "divided by this factor for correct scaling of noise and other broadband "
            "signals. 'Normalized' means w.r.t. a rectangular window. A higher NENBW "
            "means worse SNR.</span>",
            #
            "<span>Gain factor for narrowband signals, the amplitude spectrum needs to "
            "be divided by this factor for correct scaling of spectral lines.</span>",
            #
            "<span>Frequency of the first minimum. The smaller this frequency, the "
            "better the frequency resolution.</span>",
            #
            "<span>Relative amplitude of the highest sidelobe. The higher this level, "
            "the more leakage is produced in spectral analysis. For filter designs, "
            "high sidelobes create bad stopband attenuations.</span>"
        ]

        self.qfft_win_select = QFFTWinSelector(self.cur_win_dict,
            all_wins_dict=all_wins_dict, app=self.app,
            objectName=self.objectName() + '_cmb')
        self.all_wins_dict = self.qfft_win_select.all_wins_dict

        self._construct_UI()
        self.calc_win_draw()

# ------------------------------------------------------------------------------
    def closeEvent(self, event):
        """
        Catch `closeEvent` (user has tried to close the FFT window) and send a
        signal to parent to decide how to proceed.

        This can be disabled by setting `self.ignore_close_event = False` e.g.
        for instantiating the widget as a standalone window.
        """
        if self.ignore_close_event:
            event.ignore()
            self.emit({'close_event': ''})

# ------------------------------------------------------------------------------
    def process_sig_rx(self, dict_sig=None):
        """
        Process signals coming from the navigation toolbar and from sig_rx:

        - `self.calc_N`
        - `self.update_view`:
        - `self.draw`: calculate window and FFT and draw both
        """
        # logger.debug("PROCESS_SIG_RX - vis={0}, needs_calc={1}\n{2}"
        #              .format(self.isVisible(), self.needs_calc, pprint_log(dict_sig)))

        if dict_sig['id'] == id(self):
            logger.warning("Stopped infinite loop:\n{0}".format(pprint_log(dict_sig)))
            return

        elif not self.isVisible():
            self.needs_calc = True

        elif 'view_changed' in dict_sig and 'fft_win' in dict_sig['view_changed']\
                or self.needs_calc:

            self.calc_win_draw()
            self.needs_calc = False

        elif  'mpl_toolbar' in dict_sig:
            if 'home' in dict_sig['mpl_toolbar']:
                self.update_view()
            elif dict_sig['mpl_toolbar'] == 'ui_level':
                # info frame is only visible for maximum detail level
                self.frm_info.setVisible(
                    self.mplwidget.mplToolbar.a_ui_level < 1)
                # Window and control widget only becomes invisible for minimum detail level
                self.frm_controls.setVisible(self.mplwidget.mplToolbar.a_ui_level < 2)
        else:
            logger.error("Cannont process dict_sig: {0}".format(dict_sig))

# ------------------------------------------------------------------------------
    def _construct_UI(self):
        """
        Intitialize the widget, consisting of:
        - Matplotlib widget with NavigationToolbar
        - Frame with control elements
        """
        self.bfont = QFont()
        self.bfont.setBold(True)

        self.lbl_N = QLabel(to_html("N =", frmt='bi'))
        self.led_N = QLineEdit(self)
        self.led_N.setText(str(self.N_view))
        self.led_N.setMaximumWidth(qtext_width(N_x=8))
        self.led_N.setToolTip(
            "<span>Number of window data points to display.</span>")

        # By default, the enter key triggers the default 'dialog action' in QDialog
        # widgets. This would activate one of the pushbuttons if `default` wasn't False.
        self.but_log_t = QPushButton("dB", default=False, autoDefault=False,
                                     objectName="chk_log_time")
        self.but_log_t.setMaximumWidth(qtext_width(" dB "))
        self.but_log_t.setCheckable(True)
        self.but_log_t.setChecked(False)
        self.but_log_t.setToolTip("Display in dB")

        self.led_log_bottom_t = QLineEdit(self)
        self.led_log_bottom_t.setVisible(self.but_log_t.isChecked())
        self.led_log_bottom_t.setText(str(self.bottom_t))
        self.led_log_bottom_t.setMaximumWidth(qtext_width(N_x=6))
        self.led_log_bottom_t.setToolTip(
            "<span>Minimum display value for log. scale.</span>")

        self.lbl_log_bottom_t = QLabel(to_html("min =", frmt='bi'), self)
        self.lbl_log_bottom_t.setVisible(self.but_log_t.isChecked())

        self.but_norm_f = QPushButton("Max=1", default=False, autoDefault=False)
        self.but_norm_f.setCheckable(True)
        self.but_norm_f.setChecked(True)
        self.but_norm_f.setMaximumWidth(qtext_width(text=" Max=1 "))
        self.but_norm_f.setToolTip(
            "Normalize window spectrum for a maximum of 1.")

        self.but_half_f = QPushButton("0...½", default=False, autoDefault=False)
        self.but_half_f.setCheckable(True)
        self.but_half_f.setChecked(True)
        self.but_half_f.setMaximumWidth(qtext_width(text=" 0...½ "))
        self.but_half_f.setToolTip(
            "Display window spectrum in the range 0 ... 0.5 f_S.")

        # By default, the enter key triggers the default 'dialog action' in QDialog
        # widgets. This activates one of the pushbuttons.
        self.but_log_f = QPushButton("dB", default=False, autoDefault=False,
                                     objectName="chk_log_freq")
        self.but_log_f.setMaximumWidth(qtext_width(" dB "))
        self.but_log_f.setToolTip("<span>Display in dB.</span>")
        self.but_log_f.setCheckable(True)
        self.but_log_f.setChecked(True)

        self.lbl_log_bottom_f = QLabel(to_html("min =", frmt='bi'), self)
        self.lbl_log_bottom_f.setVisible(self.but_log_f.isChecked())

        self.led_log_bottom_f = QLineEdit(self)
        self.led_log_bottom_f.setVisible(self.but_log_t.isChecked())
        self.led_log_bottom_f.setText(str(self.bottom_f))
        self.led_log_bottom_f.setMaximumWidth(qtext_width(N_x=6))
        self.led_log_bottom_f.setToolTip(
            "<span>Minimum display value for log. scale.</span>")

        # ----------------------------------------------------------------------
        #               ### frm_controls ###
        #
        # This widget encompasses all control subwidgets
        # ----------------------------------------------------------------------
        layH_win_select = QHBoxLayout()
        layH_win_select.addWidget(self.qfft_win_select)
        layH_win_select.setContentsMargins(0, 0, 0, 0)
        layH_win_select.addStretch(1)
        self.frmQFFT = QFrame(self, objectName="frmQFFT")
        self.frmQFFT.setLayout(layH_win_select)

        hline = QHLine()

        layHControls = QHBoxLayout()
        layHControls.addWidget(self.lbl_N)
        layHControls.addWidget(self.led_N)
        layHControls.addStretch(1)
        layHControls.addWidget(self.lbl_log_bottom_t)
        layHControls.addWidget(self.led_log_bottom_t)
        layHControls.addWidget(self.but_log_t)
        layHControls.addStretch(5)
        layHControls.addWidget(QVLine(width=2))
        layHControls.addStretch(5)
        layHControls.addWidget(self.but_norm_f)
        layHControls.addStretch(1)
        layHControls.addWidget(self.but_half_f)
        layHControls.addStretch(1)
        layHControls.addWidget(self.lbl_log_bottom_f)
        layHControls.addWidget(self.led_log_bottom_f)
        layHControls.addWidget(self.but_log_f)

        layVControls = QVBoxLayout()
        layVControls.addWidget(self.frmQFFT)
        layVControls.addWidget(hline)
        layVControls.addLayout(layHControls)

        self.frm_controls = QFrame(self, objectName="frmControls")
        self.frm_controls.setLayout(layVControls)

        # ----------------------------------------------------------------------
        #               ### mplwidget ###
        #
        # Layout layVMainMpl (VBox) is defined within MplWidget, additional
        # widgets can be added below the matplotlib widget (here: self.frm_controls)
        #
        # ----------------------------------------------------------------------
        self.mplwidget = MplWidget(self)
        self.mplwidget.layVMainMpl.addWidget(self.frm_controls)
        self.mplwidget.layVMainMpl.setContentsMargins(0, 0, 0, 0)

        self.mplwidget.mplToolbar.a_he.setEnabled(False)  # enable help menu
        self.mplwidget.mplToolbar.a_he.info = "manual/plot_fft.html"  # TODO: missing!
        self.mplwidget.mplToolbar.a_ui_num_levels = 3 # number of ui levels

        # ----------------------------------------------------------------------
        #               ### frm_info ###
        #
        # This widget encompasses the text info box and the table with window
        # parameters.
        # ----------------------------------------------------------------------
        self.tbl_win_props = QTableWidget(self.tbl_rows, self.tbl_cols, self)
        self.tbl_win_props.setAlternatingRowColors(True)
        # Auto-resize of table can be set using the header (although it is invisible)
        self.tbl_win_props.verticalHeader().setSectionResizeMode(QHeaderView.Stretch)
        # Only the columns with data are stretched, the others are minimum size
        self.tbl_win_props.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tbl_win_props.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.tbl_win_props.verticalHeader().setVisible(False)
        self.tbl_win_props.horizontalHeader().setVisible(False)
        self.tbl_win_props.setSizePolicy(
            QSizePolicy.MinimumExpanding, QSizePolicy.MinimumExpanding)
        self.tbl_win_props.setFixedHeight(
            self.tbl_win_props.rowHeight(0) * self.tbl_rows
            + self.tbl_win_props.frameWidth() * 2)
        # self.tbl_win_props.setVerticalScrollBarPolicy(
        #     Qt.ScrollBarAlwaysOff)
        # self.tbl_win_props.setHorizontalScrollBarPolicy(
        #     Qt.ScrollBarAlwaysOff)

        self._construct_table(self.tbl_rows, self.tbl_cols, " ")

        self.txtInfoBox = QTextBrowser(self)

        layVInfo = QVBoxLayout(self)
        layVInfo.addWidget(self.tbl_win_props)
        layVInfo.addWidget(self.txtInfoBox)

        self.frm_info = QFrame(self, objectName="frmInfo")
        self.frm_info.setLayout(layVInfo)

        # ----------------------------------------------------------------------
        #               ### splitter ###
        #
        # This widget encompasses all subwidgets
        # ----------------------------------------------------------------------

        splitter = QSplitter(self)
        splitter.setOrientation(Qt.Vertical)
        splitter.addWidget(self.mplwidget)
        splitter.addWidget(self.frm_info)

        # setSizes uses absolute pixel values, but can be "misused" by
        # specifying values that are way too large: in this case, the space
        # is distributed according to the _ratio_ of the values:
        splitter.setSizes([3000, 800])

        layVMain = QVBoxLayout()
        layVMain.addWidget(splitter)
        self.setLayout(layVMain)

        # ----------------------------------------------------------------------
        #           Set subplots
        #
        self.ax = self.mplwidget.fig.subplots(nrows=1, ncols=2)
        self.ax_t = self.ax[0]
        self.ax_f = self.ax[1]

        self.calc_win_draw()  # initial calculation and drawing

        # ----------------------------------------------------------------------
        # GLOBAL SIGNALS & SLOTs
        # ----------------------------------------------------------------------
        self.sig_rx.connect(self.process_sig_rx)
        self.sig_rx.connect(self.qfft_win_select.sig_rx)

        # ----------------------------------------------------------------------
        # LOCAL SIGNALS & SLOTs
        # ----------------------------------------------------------------------
        self.but_log_f.clicked.connect(self.update_view)
        self.but_log_t.clicked.connect(self.update_view)
        self.led_log_bottom_t.editingFinished.connect(self.update_bottom)
        self.led_log_bottom_f.editingFinished.connect(self.update_bottom)

        self.led_N.editingFinished.connect(self.calc_win_draw)

        self.but_norm_f.clicked.connect(self.calc_win_draw)
        self.but_half_f.clicked.connect(self.update_view)

        self.mplwidget.mplToolbar.sig_tx.connect(self.process_sig_rx)
        self.tbl_win_props.itemClicked.connect(self._handle_item_clicked)

        self.qfft_win_select.sig_tx.connect(self.update_fft_win)

# ------------------------------------------------------------------------------

    def _construct_table(self, rows, cols, val):
        """
        Create a table with `rows` and `cols`, organized in sets of 3:
        Name (with a checkbox) - value - unit
        each item. Only called once during construction.

        Parameters
        ----------

        rows : int
            number of rows

        cols : int
            number of columns (must be multiple of 3)

        val : str
            initialization value for the table

        Returns
        -------
        None
        """
        for r in range(rows):
            for c in range(cols):
                item = QTableWidgetItem(val)
                item_num = r * 2 + c // 3
                if c % 3 == 0:
                    # Only create a checkbox and a tooltipp in the first
                    # column of each item
                    item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                    item.setToolTip(self.tooltips_tbl[item_num])
                    if self.tbl_sel[item_num]:
                        item.setCheckState(Qt.Checked)
                    else:
                        item.setCheckState(Qt.Unchecked)

                self.tbl_win_props.setItem(r, c, item)
    # https://stackoverflow.com/questions/12366521/pyqt-checkbox-in-qtablewidget

# ------------------------------------------------------------------------------
    def update_fft_win(self, dict_sig=None):
        """
        Update FFT window when window or parameters have changed and
        pass thru 'view_changed':'fft_win_type' or 'fft_win_par'
        """
        self.calc_win_draw()
        self.emit(dict_sig)

# ------------------------------------------------------------------------------
    def calc_win_draw(self):
        """
        (Re-)Calculate the window, its FFT and some characteristic values and update
        the plot of the window and its FFT. This should be triggered when the
        window type or length or a parameters has been changed.

        Returns
        -------
        None

        Attributes
        ----------

        """
        self.N_view = safe_eval(self.led_N.text(), self.N_view, sign='pos',
                                return_type='int')
        self.cur_win_dict['win_len'] = self.N_view  # store number of view point in dict
        self.led_N.setText(str(self.N_view))
        self.n = np.arange(self.N_view)
        self.win_view = self.qfft_win_select.get_window(self.N_view, sym=self.sym)

        if self.qfft_win_select.err:
            self.qfft_win_select.dict2ui()

        self.nenbw = self.N_view * np.sum(np.square(self.win_view))\
            / np.square(np.sum(self.win_view)) # normalized equiv. noise BW
        self.cgain = np.sum(self.win_view) / self.N_view  # coherent gain

        # calculate the FFT of the window with a zero padding factor
        # of `self.pad`
        self.F = fftfreq(self.N_view * self.pad, d=1. / fb.fil[0]['f_S'])
        self.Win = np.abs(fft(self.win_view, self.N_view * self.pad))

        # Correct gain for periodic signals (coherent gain)
        if self.but_norm_f.isChecked():
            self.Win /= (self.N_view * self.cgain)

        # calculate frequency of first zero and maximum sidelobe level,
        # argrelmin() returns an array with indices of relative minima
        first_zero = argrelmin(self.Win[:(self.N_view*self.pad)//2])

        if np.shape(first_zero)[1] > 0:
            first_zero = first_zero[0][0]
            self.first_zero_f = self.F[first_zero]
            self.sidelobe_level = np.max(
                self.Win[first_zero:(self.N_view*self.pad)//2])
        else:
            self.first_zero_f = np.nan
            self.sidelobe_level = 0

        self.update_view()

# ------------------------------------------------------------------------------
    def _set_table_item(self, row, col, val, font=None, sel=None):
        """
        Set the table item with the index `row, col` and the value val
        """
        item = self.tbl_win_props.item(row, col)
        item.setText(str(val))

        if font:
            self.tbl_win_props.item(row, col).setFont(font)

        if sel is True:
            item.setCheckState(Qt.Checked)
        if sel is False:
            item.setCheckState(Qt.Unchecked)
        # when sel is not specified, don't change anything

# ------------------------------------------------------------------------------
    def _handle_item_clicked(self, item):
        if item.column() % 3 == 0:  # clicked on checkbox
            num = item.row() * 2 + item.column() // 3
            if item.checkState() == Qt.Checked:
                self.tbl_sel[num] = True
                logger.debug('"{0}:{1}" Checked'.format(item.text(), num))
            else:
                self.tbl_sel[num] = False
                logger.debug('"{0}:{1}" Unchecked'.format(item.text(), num))

        elif item.column() % 3 == 1:  # clicked on value field
            logger.info("{0:s} copied to clipboard.".format(item.text()))
            fb.clipboard.setText(item.text())

        self.update_view()

# ------------------------------------------------------------------------------
    def update_bottom(self):
        """
        Update log bottom settings
        """
        self.bottom_t = safe_eval(self.led_log_bottom_t.text(), self.bottom_t,
                                  sign='neg', return_type='float')
        self.led_log_bottom_t.setText(str(self.bottom_t))

        self.bottom_f = safe_eval(self.led_log_bottom_f.text(), self.bottom_f,
                                  sign='neg', return_type='float')
        self.led_log_bottom_f.setText(str(self.bottom_f))

        self.update_view()

# ------------------------------------------------------------------------------
    def update_view(self):
        """
        Draw the figure with new limits, scale, lin/log  etc without
        recalculating the window or its FFT.
        """
        # suppress "divide by zero in log10" warnings
        old_settings_seterr = np.seterr()
        np.seterr(divide='ignore')

        self.ax_t.cla()
        self.ax_f.cla()

        self.ax_t.set_xlabel(fb.fil[0]['plt_tLabel'])
        self.ax_t.set_ylabel(r'$w[n] \; \rightarrow$')

        self.ax_f.set_xlabel(fb.fil[0]['plt_fLabel'])
        self.ax_f.set_ylabel(r'$W(f) \; \rightarrow$')

        if self.but_log_t.isChecked():
            self.ax_t.plot(self.n, np.maximum(20 * np.log10(np.abs(self.win_view)),
                                              self.bottom_t))
        else:
            self.ax_t.plot(self.n, self.win_view)

        if self.but_half_f.isChecked():
            F = self.F[:len(self.F*self.pad)//2]
            Win = self.Win[:len(self.F*self.pad)//2]
        else:
            F = fftshift(self.F)
            Win = fftshift(self.Win)

        if self.but_log_f.isChecked():
            self.ax_f.plot(F, np.maximum(
                20 * np.log10(np.abs(Win)), self.bottom_f))
            self.nenbw_disp = 10 * np.log10(self.nenbw)
            self.cgain_disp = 20 * np.log10(self.cgain)
            self.sidelobe_level_disp = 20 * np.log10(self.sidelobe_level)
            self.nenbw_unit = "dB"
            self.cgain_unit = "dB"
        else:
            self.ax_f.plot(F, Win)
            self.nenbw_disp = self.nenbw
            self.cgain_disp = self.cgain
            self.sidelobe_level_disp = self.sidelobe_level
            self.nenbw_unit = "bins"
            self.cgain_unit = ""

        self.led_log_bottom_t.setVisible(self.but_log_t.isChecked())
        self.lbl_log_bottom_t.setVisible(self.but_log_t.isChecked())
        self.led_log_bottom_f.setVisible(self.but_log_f.isChecked())
        self.lbl_log_bottom_f.setVisible(self.but_log_f.isChecked())

        cur_id = self.cur_win_dict['id']
        cur_win_d = self.all_wins_dict[cur_id]
        cur_name = cur_win_d['disp_name']

        param_txt = ""
        if len(self.cur_win_dict['par_val']) > 0:
            if type(self.cur_win_dict['par_val'][0]) in {str}:
                p1 = self.cur_win_dict['par_val'][0]
            else:
                p1 = "{0:.3g}".format(self.cur_win_dict['par_val'][0])
            param_txt = " ({0:s} = {1:s})".format(
                self.all_wins_dict[cur_id]['par'][0]['name_tex'], p1)

        if len(self.cur_win_dict['par_val']) > 1:
            if type(self.cur_win_dict['par_val'][1]) in {str}:
                p2 = self.cur_win_dict['par_val'][1]
            else:
                p2 = "{0:.3g}".format(self.cur_win_dict['par_val'][1])
            param_txt = param_txt[:-1] + ", {0:s} = {1:s})".format(
                self.all_wins_dict[cur_id]['par'][1]['name_tex'], p2)

        self.mplwidget.fig.suptitle(r'{0} Window'.format(cur_name) + param_txt)

        # plot a line at the first zero
        if self.tbl_sel[2] and not np.isnan(self.first_zero_f):
            self.ax_f.axvline(self.first_zero_f, ls='dotted', c='b')
        # plot a line at the max. sidelobe level
        if self.tbl_sel[3] and not np.isnan(self.first_zero_f):
            self.ax_f.axhline(self.sidelobe_level_disp, ls='dotted', c='b')

        patch = mpl_patches.Rectangle((0, 0), 1, 1, fc="white", ec="white",
                                      lw=0, alpha=0)
        # Info legend for time domain window
        labels_t = []
        labels_t.append("$N$ = {0:d}".format(self.N_view))
        self.ax_t.legend([patch], labels_t, loc='best', fontsize='small',
                         fancybox=True, framealpha=0.7,
                         handlelength=0, handletextpad=0)

        # Info legend for frequency domain window
        labels_f = []
        N_patches = 0
        if self.tbl_sel[0]:
            labels_f.append("$NENBW$ = {0:.4g} {1}".format(self.nenbw_disp,
                                                           self.nenbw_unit))
            N_patches += 1
        if self.tbl_sel[1]:
            labels_f.append("$CGAIN$ = {0:.4g} {1}".format(self.cgain_disp,
                                                           self.cgain_unit))
            N_patches += 1
        if self.tbl_sel[2]:
            labels_f.append("1st Zero = {0:.4g}".format(self.first_zero_f))
            N_patches += 1

        if N_patches > 0:
            self.ax_f.legend([patch] * N_patches, labels_f, loc='best',
                             fontsize='small', fancybox=True, framealpha=0.7,
                             handlelength=0, handletextpad=0)
        np.seterr(**old_settings_seterr)

        self.update_info()
        self.redraw()

# ------------------------------------------------------------------------------
    def update_info(self):
        """
        Update the text info box for the window
        """
        cur_id = self.cur_win_dict['id']
        if 'info' in self.all_wins_dict[cur_id]:
            self.txtInfoBox.setText(self.all_wins_dict[cur_id]['info'])
        else:
            self.txtInfoBox.clear()

        self._set_table_item(0, 0, "NENBW", font=self.bfont)  # , sel=True)
        self._set_table_item(0, 1, "{0:.5g}".format(self.nenbw_disp))
        self._set_table_item(0, 2, self.nenbw_unit)
        self._set_table_item(0, 3, "Correlated Gain", font=self.bfont)  # , sel=True)
        self._set_table_item(0, 4, "{0:.5g}".format(self.cgain_disp))
        self._set_table_item(0, 5, self.cgain_unit)

        self._set_table_item(1, 0, "1st Zero", font=self.bfont)  # , sel=True)
        self._set_table_item(1, 1, "{0:.5g}".format(self.first_zero_f))
        self._set_table_item(1, 2, "f_S")

        self._set_table_item(1, 3, "Max. Sidelobe", font=self.bfont)  # , sel=True)
        self._set_table_item(1, 4, "{0:.5g}".format(self.sidelobe_level_disp))
        self._set_table_item(1, 5, self.cgain_unit)

        self.tbl_win_props.resizeColumnsToContents()
        self.tbl_win_props.resizeRowsToContents()

# -----------------------------------------------------------------------------
    def redraw(self):
        """
        Redraw the canvas when e.g. the canvas size has changed
        """
        self.mplwidget.redraw()


# ==============================================================================
if __name__ == '__main__':
    """ Run widget standalone with `python -m pyfda.plot_widgets.plot_fft_win` """
    import sys
    from pyfda.libs.compat import QApplication
    from pyfda import pyfda_rc as rc

    app = QApplication(sys.argv)
    app.setStyleSheet(rc.qss_rc)
    fb.clipboard = QApplication.clipboard()  # create clipboard instance

    mainw = Plot_FFT_win(app='spec', ignore_close_event=False)

    app.setActiveWindow(mainw)
    mainw.show()

    sys.exit(app.exec_())
