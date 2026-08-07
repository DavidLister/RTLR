# rtlr.py
#
# Main for the Real Time Laser Reflectometry (RTLR) program.
# Opens GUI window and threads for interfacing with the lock-in amplifier.
#
# David Lister
# July 2023
#

import logging
import queue
import numpy as np
import time
import datetime
import os
from PySide6 import QtGui, QtCore
from PySide6.QtWidgets import QApplication, QMainWindow, QGridLayout, QWidget, QToolButton
import pyqtgraph as pg
import sys
import common
import srs830
# analysis_panel (and scipy) are imported lazily on first caret expand


logger = logging.getLogger("RTLR")
logger.setLevel(logging.DEBUG)
ch = logging.StreamHandler()
ch.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
ch.setFormatter(formatter)
logger.addHandler(ch)

logger.debug("Logger Started")


class MainWindow(QMainWindow):
    def __init__(self, data_queue, run_name, run_dir):
        super().__init__()
        self.logger = logging.getLogger("RTLR.MainWindow")
        self.logger.debug("Main window started")
        self.run_name = run_name
        self.run_dir = run_dir

        self.setWindowTitle("RTLR - Real Time Laser Reflectometry")
        self.colour = self.palette().color(QtGui.QPalette.Window)
        self.main_pen = pg.mkPen(color=(20, 20, 20))
        self.fit_pen = pg.mkPen(color=(153, 0, 0))
        self.data_queue = data_queue

        self.layout = QGridLayout()

        self.plot_reflectance = pg.PlotWidget()
        self.plot_reflectance.setBackground(self.colour)
        self.plot_reflectance.setTitle("Reflectance")
        self.plot_reflectance.setLabel("bottom", "Time (s)")
        self.plot_reflectance.setLabel("left", "Reflectance (V)")
        self.plot_reflectance.enableAutoRange()

        self.plot_raw = pg.PlotWidget()
        self.plot_raw.setBackground(self.colour)
        self.plot_raw.setTitle("Raw Signal")
        self.plot_raw.setLabel("bottom", "Time (s)")
        self.plot_raw.setLabel("left", "Voltage (V)")
        self.plot_raw.enableAutoRange()

        # Curves are created once and updated in-place with setData
        self.curve_reflectance = self.plot_reflectance.plot([], [], pen=self.main_pen)
        self.curve_raw = self.plot_raw.plot([], [], pen=self.main_pen)
        self.curve_upper_median = self.plot_raw.plot([], [], pen=self.fit_pen)
        self.curve_lower_median = self.plot_raw.plot([], [], pen=self.fit_pen)

        self.layout.addWidget(self.plot_reflectance, 0, 0, 3, 4)
        self.layout.addWidget(self.plot_raw, 3, 0, 3, 4)

        # Analysis panel: collapsed caret by default; the panel (and the
        # scipy-based fitting engine) is only created on first expand
        self.analysis_panel = None
        self.analysis_caret = QToolButton()
        self.analysis_caret.setText("Analysis")
        self.analysis_caret.setCheckable(True)
        self.analysis_caret.setArrowType(QtCore.Qt.RightArrow)
        self.analysis_caret.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.analysis_caret.toggled.connect(self.toggle_analysis_panel)
        self.layout.addWidget(self.analysis_caret, 6, 0, 1, 1)

        self.widget = QWidget()
        self.widget.setLayout(self.layout)
        self.setCentralWidget(self.widget)

        self.init_time = time.time()

        self.time = []
        self.reflectance = []
        self.save_file_name = os.path.join(self.run_dir, "Reflectance.csv")

        self.raw_time = []
        self.raw_voltage = []
        self.upper_median = None
        self.lower_median = None

        if common.SAVE_CALCULATED_REFLECTANCE:
            header = "Time (s),Reflectance (V)"
            if common.SRS830_CAPTURE_CH2:
                header += ",CH2"
            with open(self.save_file_name, 'w') as f:
                f.write(header + "\n")

        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update_graphs)
        self.timer.start(common.WINDOW_UPDATE_RATE_MS)

    def toggle_analysis_panel(self, expanded):
        self.analysis_caret.setArrowType(
            QtCore.Qt.DownArrow if expanded else QtCore.Qt.RightArrow)
        if self.analysis_panel is None and expanded:
            from analysis_panel import AnalysisPanel
            self.analysis_panel = AnalysisPanel(self.run_dir, self.plot_reflectance)
            self.layout.addWidget(self.analysis_panel, 7, 0, 1, 4)
            self.logger.info("Analysis panel created")
        if self.analysis_panel is not None:
            self.analysis_panel.setVisible(expanded)

    def update_graphs(self):
        data_added = False
        while not self.data_queue.empty():
            data = self.data_queue.get_nowait()
            capture_time = data[0] - self.init_time
            self.raw_time, self.raw_voltage, raw_ch2 = data[1]

            r = self.raw_voltage
            mean = np.mean(r)
            stdev = np.std(r)
            upper = r[r > mean + stdev / 2]
            lower = r[r < mean - stdev / 2]
            if len(upper) == 0 or len(lower) == 0:
                self.logger.warning(f"Capture at t={capture_time:.2f} has no modulation (flat signal?), skipping")
                continue
            self.upper_median = np.median(upper)
            self.lower_median = np.median(lower)

            match common.CALC_TYPE:
                case common.CALC_PEAK_TO_PEAK:
                    reflectance = self.upper_median - self.lower_median

                case common.CALC_UPPER_MEDIAN:
                    reflectance = self.upper_median

                case _:
                    self.logger.error(f"Unknown CALC_TYPE {common.CALC_TYPE}, skipping point")
                    continue

            self.logger.debug(f"Reflectance value calculated: t={capture_time} R={reflectance}")
            self.time.append(capture_time)
            self.reflectance.append(reflectance)
            data_added = True

            if common.SAVE_CALCULATED_REFLECTANCE:
                row = f"{capture_time},{reflectance}"
                if common.SRS830_CAPTURE_CH2:
                    row += f",{np.mean(raw_ch2)}"
                with open(self.save_file_name, 'a') as f:
                    f.write(row + "\n")

            if self.analysis_panel is not None:
                self.analysis_panel.feed(capture_time, reflectance, float(np.mean(raw_ch2)))

        if data_added:
            self.curve_reflectance.setData(self.time, self.reflectance)
            self.curve_raw.setData(self.raw_time, self.raw_voltage)
            ends = [self.raw_time[0], self.raw_time[-1]]
            self.curve_upper_median.setData(ends, [self.upper_median] * 2)
            self.curve_lower_median.setData(ends, [self.lower_median] * 2)

        if self.analysis_panel is not None and self.analysis_panel.isVisible():
            self.analysis_panel.refresh()


if __name__ == "__main__":
    replay_file = sys.argv[1] if len(sys.argv) > 1 else common.RTLR_REPLAY_FILE
    replay_speed = float(sys.argv[2]) if len(sys.argv) > 2 else common.RTLR_REPLAY_SPEED

    run_name = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + "---ADD_RUN_INFO_HERE"
    run_dir = os.path.join(common.DATA_SUBPATH, run_name)
    os.makedirs(run_dir, exist_ok=True)
    queue_srs_to_analysis = queue.Queue()
    queue_srs_commands = queue.Queue()

    if replay_file is not None:
        import replay
        srs830_handler = replay.ReplayHandler(queue_srs_to_analysis, queue_srs_commands,
                                              run_name, run_dir,
                                              replay_file=replay_file, speed=replay_speed)
    else:
        srs830_handler = srs830.SRS830Handler(queue_srs_to_analysis, queue_srs_commands, run_name, run_dir, serial_port=common.SRS830_COM_PORT)

    app = QApplication([])

    window = MainWindow(queue_srs_to_analysis, run_name, run_dir)
    window.show()
    over = app.exec()

    # Cleanup and close
    window.timer.stop()
    if window.analysis_panel is not None:
        window.analysis_panel.shutdown()
    srs830_handler.stop()
    srs830_handler.join(timeout=10)
    if srs830_handler.p.is_alive():
        logger.error("SRS830 thread did not shut down cleanly within 10s")
    sys.exit(over)
