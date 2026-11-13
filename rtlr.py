# rtlr.py
#
# Main for the Real Time Laser Reflectometry (RTLR) program.
# Opens GUI window and threads for interfacing with the lock-in amplifier.
#
# Nothing connects or logs until the operator picks a serial port, enters a
# run name, and presses Start. The run name goes into both the run directory
# and the reflectance file name, so every saved file is unique on its own.
#
# David Lister
# July 2023
#

import logging
import queue
import re
import numpy as np
import time
import datetime
import os
from PySide6 import QtGui, QtCore
from PySide6.QtWidgets import (QApplication, QMainWindow, QGridLayout, QHBoxLayout, QWidget,
                               QToolButton, QLabel, QComboBox, QLineEdit, QPushButton)
import pyqtgraph as pg
from serial.tools import list_ports
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

# Persistent settings (Windows registry / ~/.config/RTLR/RTLR.conf on Linux)
SETTINGS_ORG = "RTLR"
SETTINGS_APP = "RTLR"
SETTING_LAST_PORT = "serial/last_working_port"

RUN_NAME_MAX_LEN = 64

CH2_COLOUR = "#1f6fb4"  # blue: distinct from the black reflectance and red median/fit lines


def sanitize_run_name(text):
    """Make a run name safe for file names on Windows and Linux: whitespace
    becomes '_', only letters, digits, '.', '_' and '-' are kept, and
    leading/trailing dots are dropped (Windows rejects trailing dots)."""
    name = re.sub(r"\s+", "_", text.strip())
    name = re.sub(r"[^A-Za-z0-9._-]", "", name)
    return name.strip(".")[:RUN_NAME_MAX_LEN]


def make_run_paths(name, now=None):
    """(run_name, run_dir, reflectance_path) for a sanitized run name."""
    stamp = (now or datetime.datetime.now()).strftime("%Y-%m-%d_%H-%M-%S")
    run_name = f"{stamp}---{name}"
    run_dir = os.path.join(common.DATA_SUBPATH, run_name)
    return run_name, run_dir, os.path.join(run_dir, f"{run_name}_Reflectance.csv")


def available_ports():
    """[(device, label)] for every serial port pyserial can see (COMx on
    Windows, /dev/tty* on Linux), real adapters before legacy placeholders."""
    ports = sorted(list_ports.comports(), key=lambda p: (p.hwid in ("", "n/a"), p.device))
    out = []
    for p in ports:
        desc = p.description if p.description not in ("", "n/a", p.device) else None
        out.append((p.device, f"{p.device} - {desc}" if desc else p.device))
    return out


def load_last_port():
    return QtCore.QSettings(SETTINGS_ORG, SETTINGS_APP).value(SETTING_LAST_PORT, common.SRS830_COM_PORT)


def save_last_port(port):
    QtCore.QSettings(SETTINGS_ORG, SETTINGS_APP).setValue(SETTING_LAST_PORT, port)


class MainWindow(QMainWindow):
    def __init__(self, handler_factory, source_label=None):
        """handler_factory(data_queue, command_queue, run_name, run_dir, serial_port)
        builds the data source when Start is pressed. source_label is None for a
        live instrument (port selectable), else a description of the fixed
        source (replay file / fake data) and the port selector is disabled."""
        super().__init__()
        self.logger = logging.getLogger("RTLR.MainWindow")
        self.logger.debug("Main window started")
        self.handler_factory = handler_factory
        self.port_selectable = source_label is None
        self.handler = None
        self.run_name = None
        self.run_dir = None
        self.save_file_name = None
        self.saved_port = None

        self.data_queue = queue.Queue()
        self.command_queue = queue.Queue()

        self.setWindowTitle("RTLR - Real Time Laser Reflectometry")
        self.colour = self.palette().color(QtGui.QPalette.Window)
        self.main_pen = pg.mkPen(color=(20, 20, 20))
        self.fit_pen = pg.mkPen(color=(153, 0, 0))

        self.layout = QGridLayout()

        # Run setup bar: serial port, run name, start
        self.port_box = QComboBox()
        self.port_box.setEditable(True)  # allow typing a path that isn't listed
        self.port_box.setMinimumWidth(280)
        self.port_box.setInsertPolicy(QComboBox.NoInsert)
        self.port_refresh_btn = QToolButton()
        self.port_refresh_btn.setText("↻")
        self.port_refresh_btn.setToolTip("Rescan serial ports")
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Required, e.g. S404")
        self.name_edit.setMaxLength(RUN_NAME_MAX_LEN)
        self.name_edit.setValidator(QtGui.QRegularExpressionValidator(
            QtCore.QRegularExpression(r"[A-Za-z0-9 _.\-]*")))
        self.start_btn = QPushButton("Start logging")
        self.start_btn.setEnabled(False)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Serial port:"))
        bar.addWidget(self.port_box)
        bar.addWidget(self.port_refresh_btn)
        bar.addSpacing(20)
        bar.addWidget(QLabel("Run name:"))
        bar.addWidget(self.name_edit, 1)
        bar.addWidget(self.start_btn)
        self.layout.addLayout(bar, 0, 0, 1, 4)

        if self.port_selectable:
            self.populate_ports(load_last_port())
        else:
            self.port_box.addItem(source_label, None)
            self.port_box.setEnabled(False)
            self.port_refresh_btn.setEnabled(False)

        self.port_refresh_btn.clicked.connect(lambda: self.populate_ports(self.selected_port()))
        self.port_box.activated.connect(self.on_port_chosen)
        self.port_box.lineEdit().editingFinished.connect(self.on_port_chosen)
        self.port_box.currentTextChanged.connect(self.update_start_enabled)
        self.name_edit.textChanged.connect(self.update_start_enabled)
        self.name_edit.returnPressed.connect(self.start_logging)
        self.start_btn.clicked.connect(self.start_logging)

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

        # CH2 (pyrometer photodiode) on its own right-hand axis, sharing the
        # time axis, so both traces keep a readable scale
        self.ch2 = []
        self.show_ch2 = common.PLOT_CH2 and common.SRS830_CAPTURE_CH2
        if self.show_ch2:
            plot_item = self.plot_reflectance.getPlotItem()
            self.ch2_view = pg.ViewBox()
            plot_item.showAxis("right")
            plot_item.scene().addItem(self.ch2_view)
            right_axis = plot_item.getAxis("right")
            right_axis.linkToView(self.ch2_view)
            right_axis.setLabel("CH2 (V)", color=CH2_COLOUR)
            right_axis.setPen(pg.mkPen(CH2_COLOUR))
            right_axis.setTextPen(pg.mkPen(CH2_COLOUR))
            self.ch2_view.setXLink(plot_item)
            self.curve_ch2 = pg.PlotCurveItem(pen=pg.mkPen(CH2_COLOUR))
            self.ch2_view.addItem(self.curve_ch2)
            plot_item.vb.sigResized.connect(self._sync_ch2_view)

        self.layout.addWidget(self.plot_reflectance, 1, 0, 3, 4)
        self.layout.addWidget(self.plot_raw, 4, 0, 3, 4)

        # Analysis panel: collapsed caret by default; the panel (and the
        # scipy-based fitting engine) is only created on first expand, and
        # only once a run has started (it writes into the run directory)
        self.analysis_panel = None
        self.analysis_caret = QToolButton()
        self.analysis_caret.setText("Analysis")
        self.analysis_caret.setCheckable(True)
        self.analysis_caret.setArrowType(QtCore.Qt.RightArrow)
        self.analysis_caret.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.analysis_caret.setEnabled(False)
        self.analysis_caret.setToolTip("Available once logging has started")
        self.analysis_caret.toggled.connect(self.toggle_analysis_panel)
        self.layout.addWidget(self.analysis_caret, 7, 0, 1, 1)

        # Event log: timestamped process notes (setpoints, thermocouple
        # readings, valve changes) on the same clock as the reflectance file
        self.events_file_name = None
        self.event_edit = QLineEdit()
        self.event_edit.setPlaceholderText("Event note, Enter to log (e.g. 'ramp to 1050 C', 'TC 1043 C', 'TMGa on')")
        self.event_edit.setEnabled(False)
        self.event_edit.returnPressed.connect(self.log_event)
        self.layout.addWidget(self.event_edit, 7, 1, 1, 3)

        self.widget = QWidget()
        self.widget.setLayout(self.layout)
        self.setCentralWidget(self.widget)
        self.statusBar().showMessage("Choose a serial port and enter a run name, then press Start logging")

        self.init_time = time.time()

        self.time = []
        self.reflectance = []

        self.raw_time = []
        self.raw_voltage = []
        self.upper_median = None
        self.lower_median = None

        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update_graphs)
        self.timer.start(common.WINDOW_UPDATE_RATE_MS)

    # ---- run setup -----------------------------------------------------
    def populate_ports(self, preferred=None):
        """Rescan ports; keep `preferred` selected even if it isn't detected
        right now (e.g. adapter unplugged), so the last working port sticks."""
        self.port_box.blockSignals(True)
        self.port_box.clear()
        for device, label in available_ports():
            self.port_box.addItem(label, device)
        if preferred and self.port_box.findData(preferred) < 0:
            self.port_box.addItem(f"{preferred} (not detected)", preferred)
        idx = self.port_box.findData(preferred) if preferred else 0
        self.port_box.setCurrentIndex(max(idx, 0))
        self.port_box.blockSignals(False)
        self.update_start_enabled()

    def selected_port(self):
        """Device path for the current selection, or the raw typed text."""
        text = self.port_box.currentText().strip()
        idx = self.port_box.findText(text)
        if idx >= 0 and self.port_box.itemData(idx):
            return self.port_box.itemData(idx)
        return text

    def update_start_enabled(self, *_):
        ok = (self.handler is None
              and bool(sanitize_run_name(self.name_edit.text()))
              and (not self.port_selectable or bool(self.selected_port())))
        self.start_btn.setEnabled(ok)

    def on_port_chosen(self, *_):
        # Before Start this just updates the selection. After Start, while the
        # handler has not connected, switch it to the newly chosen port.
        if self.handler is None or not self.port_selectable or self.handler.connected_port:
            return
        port = self.selected_port()
        if port and port != getattr(self.handler, "serialPort", None):
            self.logger.info(f"Switching serial port to {port}")
            self.command_queue.put((common.SRS830_COMMAND_SET_SERIAL_PORT, port))

    def start_logging(self):
        if self.handler is not None:
            return
        name = sanitize_run_name(self.name_edit.text())
        if not name:
            self.statusBar().showMessage("Enter a run name before logging can start")
            self.name_edit.setFocus()
            return
        port = self.selected_port() if self.port_selectable else None
        if self.port_selectable and not port:
            self.statusBar().showMessage("Choose a serial port before logging can start")
            return

        run_name, run_dir, refl_path = make_run_paths(name)
        try:
            os.makedirs(run_dir)
        except FileExistsError:
            self.statusBar().showMessage(f"{run_dir} already exists - wait a second and press Start again")
            return

        self.run_name, self.run_dir, self.save_file_name = run_name, run_dir, refl_path
        if common.SAVE_CALCULATED_REFLECTANCE:
            header = "Time (s),Reflectance (V)"
            if common.SRS830_CAPTURE_CH2:
                header += ",CH2"
            with open(self.save_file_name, 'w') as f:
                f.write(header + "\n")

        self.name_edit.setText(name)  # show exactly what was used
        self.name_edit.setEnabled(False)
        self.start_btn.setEnabled(False)
        self.start_btn.setText("Logging")
        self.analysis_caret.setEnabled(True)
        self.analysis_caret.setToolTip("")
        self.events_file_name = os.path.join(run_dir, f"{run_name}_events.csv")
        with open(self.events_file_name, 'w') as f:
            f.write("Time (s),Local time,Event\n")
        self.event_edit.setEnabled(True)
        self.setWindowTitle(f"RTLR - {run_name}")

        self.handler = self.handler_factory(self.data_queue, self.command_queue, run_name, run_dir, port)
        # Share the data source's clock so its timestamps (and any raw debug
        # files) line up exactly with the reflectance file
        self.init_time = getattr(self.handler, "time_origin", time.time())
        self.logger.info(f"Logging started: {self.save_file_name}" + (f" from {port}" if port else ""))

    def log_event(self):
        text = self.event_edit.text().strip()
        if not text or self.events_file_name is None:
            return
        t = time.time() - self.init_time
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        safe = text.replace('"', "'")
        try:
            with open(self.events_file_name, 'a') as f:
                f.write(f'{t:.1f},{stamp},"{safe}"\n')
        except OSError as err:
            self.statusBar().showMessage(f"Could not write event: {err}")
            return
        marker = pg.InfiniteLine(pos=t, angle=90, pen=pg.mkPen((110, 110, 110), style=QtCore.Qt.DashLine),
                                 label=text, labelOpts={"position": 0.85, "color": (60, 60, 60),
                                                        "fill": (240, 240, 240, 200),
                                                        "anchors": [(0, 0.5), (0, 0.5)]})
        self.plot_reflectance.addItem(marker)
        self.event_edit.clear()
        self.logger.info(f"Event at t={t:.1f}s: {text}")

    def poll_handler_status(self):
        if self.handler is None:
            return
        connected = self.handler.connected_port
        if self.port_selectable:
            # Port can be changed until the instrument answers, and again if
            # the connection drops.
            self.port_box.setEnabled(not connected)
            self.port_refresh_btn.setEnabled(not connected)
            if connected and connected != self.saved_port:
                save_last_port(connected)
                self.saved_port = connected
                self.logger.info(f"Saved {connected} as the default serial port")
        msg = f"{self.run_name}  |  {self.handler.status}"
        if self.statusBar().currentMessage() != msg:
            self.statusBar().showMessage(msg)

    def shutdown(self):
        self.timer.stop()
        if self.analysis_panel is not None:
            self.analysis_panel.shutdown()
        if self.handler is not None:
            self.handler.stop()
            self.handler.join(timeout=10)
            if self.handler.p.is_alive():
                self.logger.error("Data source thread did not shut down cleanly within 10s")

    # ---- display -------------------------------------------------------
    def _sync_ch2_view(self):
        main_vb = self.plot_reflectance.getPlotItem().vb
        self.ch2_view.setGeometry(main_vb.sceneBoundingRect())
        self.ch2_view.linkedViewChanged(main_vb, self.ch2_view.XAxis)

    def toggle_analysis_panel(self, expanded):
        self.analysis_caret.setArrowType(
            QtCore.Qt.DownArrow if expanded else QtCore.Qt.RightArrow)
        if self.analysis_panel is None and expanded and self.run_dir is not None:
            from analysis_panel import AnalysisPanel
            self.analysis_panel = AnalysisPanel(self.run_dir, self.plot_reflectance)
            self.layout.addWidget(self.analysis_panel, 8, 0, 1, 4)
            self.logger.info("Analysis panel created")
        if self.analysis_panel is not None:
            self.analysis_panel.setVisible(expanded)

    def update_graphs(self):
        self.poll_handler_status()
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
            ch2_value = float(np.mean(raw_ch2))
            self.time.append(capture_time)
            self.reflectance.append(reflectance)
            self.ch2.append(ch2_value)
            data_added = True

            if common.SAVE_CALCULATED_REFLECTANCE:
                row = f"{capture_time},{reflectance}"
                if common.SRS830_CAPTURE_CH2:
                    row += f",{ch2_value}"
                with open(self.save_file_name, 'a') as f:
                    f.write(row + "\n")

            if self.analysis_panel is not None:
                self.analysis_panel.feed(capture_time, reflectance, ch2_value)

        if data_added:
            self.curve_reflectance.setData(self.time, self.reflectance)
            if self.show_ch2:
                self.curve_ch2.setData(self.time, self.ch2)
            self.curve_raw.setData(self.raw_time, self.raw_voltage)
            ends = [self.raw_time[0], self.raw_time[-1]]
            self.curve_upper_median.setData(ends, [self.upper_median] * 2)
            self.curve_lower_median.setData(ends, [self.lower_median] * 2)

        if self.analysis_panel is not None and self.analysis_panel.isVisible():
            self.analysis_panel.refresh()


def build_source():
    """(handler_factory, source_label) from the command line / common.py."""
    replay_file = sys.argv[1] if len(sys.argv) > 1 else common.RTLR_REPLAY_FILE
    replay_speed = float(sys.argv[2]) if len(sys.argv) > 2 else common.RTLR_REPLAY_SPEED

    if replay_file is not None:
        import replay

        def factory(data_q, cmd_q, run_name, run_dir, port):
            return replay.ReplayHandler(data_q, cmd_q, run_name, run_dir,
                                        replay_file=replay_file, speed=replay_speed)
        return factory, f"Replay: {os.path.basename(replay_file)} at {replay_speed:g}x"

    def factory(data_q, cmd_q, run_name, run_dir, port):
        return srs830.SRS830Handler(data_q, cmd_q, run_name, run_dir, serial_port=port)
    return factory, ("Fake serial data" if common.SRS830_FAKE_SERIAL else None)


if __name__ == "__main__":
    handler_factory, source_label = build_source()

    app = QApplication([])
    window = MainWindow(handler_factory, source_label)
    window.show()
    over = app.exec()

    # Cleanup and close
    window.shutdown()
    sys.exit(over)
