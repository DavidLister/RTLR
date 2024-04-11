# rtlr.py
#
# Main for the Real Time Laser Reflectometry (RTLR) program.
# Opens GUI window and threads for interfacing with the lock-in amplifier.
#
# David Lister
# July 2023
#

import logging
#import multiprocessing
import queue
import numpy as np
import time
import datetime
import os
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
    def __init__(self, data_queue, run_name, run_dir):
        super().__init__()
        self.logger = logging.getLogger("ProgramName.MainWindow")
        self.logger.debug("Main window started")
        self.run_name = run_name
        self.run_dir = run_dir

        self.setWindowTitle("Program Name")
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


        self.layout.addWidget(self.plot_reflectance, 0, 0, 3, 4)
        self.layout.addWidget(self.plot_raw, 3, 0, 3, 4)

        self.widget = QWidget()
        self.widget.setLayout(self.layout)
        self.setCentralWidget(self.widget)

        self.init_time = time.time()

        self.time = []
        self.reflectance = []
        self.save_file_name = os.path.join(self.run_dir, "Reflectance.csv")

        self.raw_time = []
        self.raw_voltage = []

        if common.SAVE_CALCULATED_REFLECTANCE:
            with open(self.save_file_name, 'w') as f:
                f.write("Time (s),Reflectance (v)\n")
        
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update_graphs)
        self.timer.start(common.WINDOW_UPDATE_RATE_MS)


    def update_graphs(self):
        data_added = False
        if not common.SRS830_FAKE_SERIAL:
            if not self.data_queue.empty():
                print("Data in the queue!")
                data_added = True
                data = self.data_queue.get_nowait()
                self.time.append(data[0] - self.init_time)
                self.raw_time = data[1][0]
                self.raw_voltage = data[1][1]

                r = self.raw_voltage
                mean = np.mean(r)
                stdev = np.std(r)
                upper_median = np.median(r[r > mean + stdev/2])
                lower_median = np.median(r[r < mean - stdev/2])
                match common.CALC_TYPE:
                    case common.CALC_PEAK_TO_PEAK:
                        self.reflectance.append(upper_median - lower_median)
                        print(f"Pk-Pk Value Calculated: t={data[0] - self.init_time} R={upper_median - lower_median}")

                    case common.CALC_UPPER_MEDIAN:
                        self.reflectance.append(upper_median)
                        print(f"Average Value Calculated: t={data[0] - self.init_time} R={upper_median}")
            

                if common.SAVE_CALCULATED_REFLECTANCE:
                    with open(self.save_file_name, 'a') as f:
                        f.write(f"{self.time[-1]},{self.reflectance[-1]}\n")
        


        else:
            self.logger.info("Adding fake data!")
            data_added = True
            self.time.append(time.time() - self.init_time)
            self.reflectance.append(random.gauss(1, 0.3))

        if data_added:
            self.plot_reflectance.clear()
            self.plot_reflectance.plot(self.time, self.reflectance, pen=self.main_pen)

            self.plot_raw.clear()
            self.plot_raw.plot(self.raw_time, self.raw_voltage, pen=self.main_pen)

            if not common.SRS830_FAKE_SERIAL:
                self.plot_raw.plot([self.raw_time[0], self.raw_time[-1]], [upper_median] * 2, pen=self.fit_pen), self.raw_time[0]
                self.plot_raw.plot([self.raw_time[0], self.raw_time[-1]], [lower_median] * 2, pen=self.fit_pen), self.raw_time[0]
        


if __name__ == "__main__":
    run_name = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + "---ADD_RUN_INFO_HERE"
    run_dir = os.path.join(common.DATA_SUBPATH, run_name)
    os.makedirs(run_dir)
    queue_srs_to_analysis = queue.Queue()
    queue_srs_commands = queue.Queue()

    srs830_handler = srs830.SRS830Handler(queue_srs_to_analysis, queue_srs_commands, run_name, run_dir, serial_port=common.SRS830_COM_PORT)

    app = QApplication([])

    window = MainWindow(queue_srs_to_analysis, run_name, run_dir)
    window.show()
    over = app.exec()

    # Cleanup and close
    window.timer.stop()
    queue_srs_commands.put(common.SRS830_COMMAND_RAISE_END_FLAG)
    srs830_handler.join()
    sys.exit(over)
