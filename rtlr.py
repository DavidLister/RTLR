# rtlr.py
#
# Main for the Real Time Laser Reflectometry (RTLR) program.
# Opens GUI window and threads for interfacing with the lock-in amplifier.
#
# David Lister
# July 2023
#

import logging
import multiprocessing
import numpy as np
import time
from PySide6 import QtGui, QtCore
from PySide6.QtWidgets import QApplication, QMainWindow, QPushButton, QGridLayout, QLabel, QWidget
import pyqtgraph as pg
import sys
import random
import common
import srs830


logger = logging.getLogger("RTLR")
logger.setLevel(logging.DEBUG)
ch = logging.StreamHandler()
ch.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
ch.setFormatter(formatter)
logger.addHandler(ch)

logger.debug("Logger Started")
class MainWindow(QMainWindow):
    def __init__(self, data_queue):
        super().__init__()
        self.logger = logging.getLogger("ProgramName.MainWindow")
        self.logger.debug("Main window started")

        self.setWindowTitle("Program Name")
        self.colour = self.palette().color(QtGui.QPalette.Window)
        self.main_pen = pg.mkPen(color=(20, 20, 20))
        self.fit_pen = pg.mkPen(color=(153, 0, 0))
        self.data_queue = data_queue

        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update_graphs)
        self.timer.start(common.WINDOW_UPDATE_RATE_MS)

        self.layout = QGridLayout()

        self.plot_reflectance = pg.PlotWidget()
        self.plot_reflectance.setBackground(self.colour)
        self.plot_reflectance.setTitle("Reflectance")
        self.plot_reflectance.setLabel("bottom", "Time (s)")
        self.plot_reflectance.setLabel("left", "Reflectance (mV)")
        self.plot_reflectance.enableAutoRange()

        self.layout.addWidget(self.plot_reflectance, 0, 0, 3, 4)

        self.widget = QWidget()
        self.widget.setLayout(self.layout)
        self.setCentralWidget(self.widget)

        self.init_time = time.time()
        self.time = []
        self.reflectance = []


    def update_graphs(self):
        if not common.SRS830_FAKE_SERIAL:
            if not self.data_queue.empty():
                data = self.data_queue.get()
                self.time.append(data[0] - self.init_time)

                r = np.array(data[1][1])
                mean = np.mean(r)
                stdev = np.std(r)
                upper_median = np.median(r[r > mean + stdev])
                lower_median = np.median(r[r < mean - stdev])
                self.reflectance.append(upper_median - lower_median)

        else:
            self.logger.info("Adding fake data!")
            self.time.append(time.time() - self.init_time)
            self.reflectance.append(random.gauss(1, 0.3))
            self.plot_reflectance.clear()
            self.plot_reflectance.plot(self.time, self.reflectance, pen=self.main_pen)


if __name__ == "__main__":
    queue_srs_to_analysis = multiprocessing.SimpleQueue()
    queue_srs_commands = multiprocessing.SimpleQueue()

    srs830_handler = srs830.SRS830Handler(queue_srs_to_analysis, queue_srs_commands, serial_port=common.SRS830_COM_PORT)
    print("Done!")

    app = QApplication([])

    window = MainWindow(queue_srs_to_analysis)
    window.show()
    over = app.exec()

    # Cleanup and close
    running = False
    queue_srs_commands.put(common.SRS830_COMMAND_RAISE_END_FLAG)
    srs830_handler.join()
    sys.exit(over)
