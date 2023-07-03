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
from PySide6 import QtGui, QtCore
from PySide6.QtWidgets import QApplication, QMainWindow, QPushButton, QGridLayout, QLabel, QWidget
import sys

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
    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger("ProgramName.MainWindow")
        self.logger.debug("Main window started")

        self.setWindowTitle("Program Name")
        self.colour = self.palette().color(QtGui.QPalette.Window)

        self.layout = QGridLayout()

        self.text = QLabel()
        self.text.setText("Don't push the button")

        self.button_example = QPushButton("Push Here")

        self.button_example.clicked.connect(self.button_press)

        self.layout.addWidget(self.text, 0, 0)  # Placement is row, col
        self.layout.addWidget(self.button_example, 1, 0)

        self.widget = QWidget()

        self.widget.setLayout(self.layout)
        self.setCentralWidget(self.widget)

    def button_press(self):
        self.text.setText("You pressed it :(")


if __name__ == "__main__":
    queue_srs_to_analysis = multiprocessing.SimpleQueue()
    queue_srs_commands = multiprocessing.SimpleQueue()

    srs830_handler = srs830.SRS830Handler(queue_srs_to_analysis, queue_srs_commands)
    print("Done!")

    app = QApplication([])

    window = MainWindow()
    window.show()
    over = app.exec()

    queue_srs_commands.put(common.SRS830_COMMAND_RAISE_END_FLAG)
    srs830_handler.join()
    sys.exit(over)
