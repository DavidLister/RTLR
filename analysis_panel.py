# analysis_panel.py
#
# Optional GUI analysis panel for RTLR: substrate calibration, segment
# marking (deposition / etch), live virtual-interface fitting readouts
# (thickness, growth rate, roughness), and the live pyrometer controls.
#
# Temperature comes from pyrometer.LivePyrometer (owned by the main window):
# CH2 minus the laser leak, Wien at ~1.2 um. The 650 nm (1 - R) emissivity
# correction is not used: the thermal signal is near-IR light the 650 nm
# bandpass does not block, so the 650 nm reflectance does not describe it.
#
# Nothing in this module is imported or instantiated until the user expands
# the analysis caret in the main window, so the capture pipeline is untouched
# when the panel is unused. Fitting runs in its own worker thread; the GUI
# only polls a snapshot on its existing update timer.
#
# David Lister
# August 2026

import os
import csv
import json
import queue
import logging
import threading

import numpy as np
from PySide6 import QtCore
from PySide6.QtWidgets import (QWidget, QGridLayout, QLabel, QComboBox,
                               QPushButton, QDoubleSpinBox)
import pyqtgraph as pg

import analysis
import materials

logger = logging.getLogger("RTLR.analysis_panel")

DEP_BRUSH = (0, 150, 0, 35)
ETCH_BRUSH = (200, 80, 0, 45)
MARKER_PEN = (120, 120, 120)


class AnalysisController:
    """Owns the fitting state on a worker thread. GUI talks to it through a
    message queue and reads results via snapshot()."""

    def __init__(self, run_dir):
        self.run_dir = run_dir
        self.q = queue.Queue()
        self.lock = threading.Lock()

        # worker-owned state
        self.substrate = "Al2O3"
        self.A = None
        self.history = []           # (t, v, ch2)
        self.segments = []          # closed + open Segment objects
        self.active = None          # (Segment, SegmentAnalyzer)
        self.prev_by_material = {}
        self.roughened = False
        self._csv_header_written = False

        # GUI-visible snapshot (guarded by lock)
        self.latest = None          # last FitResult
        self.model_points = ([], [])
        self.status = "Panel active - calibrate on bare substrate"

        self.event_end = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    # ---- GUI-thread API ----------------------------------------------------
    def feed(self, t, v, ch2):
        self.q.put(("point", t, v, ch2))

    def command(self, *msg):
        self.q.put(msg)

    def snapshot(self):
        with self.lock:
            return {"latest": self.latest, "A": self.A,
                    "model": self.model_points, "status": self.status,
                    "segments": list(self.segments)}

    def set_status(self, text):
        with self.lock:
            self.status = text

    def stop(self):
        self.event_end.set()
        self.q.put(("noop",))

    # ---- worker ----------------------------------------------------------
    def _run(self):
        while not self.event_end.is_set():
            try:
                msg = self.q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._handle(msg)
            except Exception as err:
                logger.exception(f"Analysis worker error on {msg[0]}: {err}")
                with self.lock:
                    self.status = f"Analysis error: {err}"

    def _handle(self, msg):
        kind = msg[0]
        if kind == "point":
            self._on_point(*msg[1:])
        elif kind == "substrate":
            self.substrate = msg[1]
        elif kind == "calibrate":
            self._calibrate(msg[1])
        elif kind == "dep_start":
            self._start_segment("deposition", msg[1], msg[2])
        elif kind == "etch_start":
            self._start_segment("etch", msg[1], msg[2])
        elif kind in ("dep_end", "etch_end"):
            self._end_segment()

    def _now(self):
        return self.history[-1][0] if self.history else 0.0

    def _recent(self, n=12):
        return self.history[-n:]

    def _on_point(self, t, v, ch2):
        self.history.append((t, v, ch2))
        result = None
        if self.active is not None:
            seg, ana = self.active
            result = ana.add_point(t, v)
            if result is not None:
                self._append_result_csv(seg, result)
        with self.lock:
            if result is not None:
                self.latest = result
            if self.active is not None and self.active[1].results:
                self.model_points = self.active[1].model_curve()

    def _calibrate(self, temp_c):
        if len(self.history) < 3:
            with self.lock:
                self.status = "Not enough data to calibrate yet"
            return
        level = float(np.median([h[1] for h in self._recent()]))
        R = analysis.bare_substrate_reflectance(self.substrate, temp_c)
        self.A = level / R
        with self.lock:
            self.status = (f"Calibrated: {level:.4f} V on {self.substrate} "
                           f"(R={R:.4f}) -> A={self.A:.4f}")
        logger.info(self.status)

    def _start_segment(self, kind, material, temp_c):
        if self.A is None:
            with self.lock:
                self.status = "Calibrate on bare substrate before marking segments"
            return
        self._end_segment()
        t = self._now()
        seg = analysis.Segment(kind=kind, t_start=t, t_end=None,
                               material=material, temp_c=temp_c)
        d0, rv_init = 0.0, None
        prev = self.prev_by_material.get(material)
        if kind == "etch" and prev is not None and prev.results:
            d0 = prev.results[-1].d_nm
            rv_init = prev.r_v
        ana = analysis.SegmentAnalyzer(
            seg, self.A, self.substrate, d0_nm=d0, r_v_init=rv_init,
            start_rough=(kind == "deposition" and self.roughened))
        self.segments.append(seg)
        self.active = (seg, ana)
        with self.lock:
            self.status = f"{kind} of {material} started at t={t:.0f}s"
        self._save_segments()

    def _end_segment(self):
        if self.active is None:
            return
        seg, ana = self.active
        seg.t_end = self._now()
        self.prev_by_material[seg.material] = ana
        if ana.roughening_onset_t is not None or \
                (ana.results and "ROUGHENING" in ana.results[-1].flags):
            self.roughened = True
        elif seg.kind == "deposition" and ana.results:
            self.roughened = False
        self.active = None
        with self.lock:
            self.status = f"{seg.kind} of {seg.material} ended at t={seg.t_end:.0f}s"
        self._save_segments()

    def _save_segments(self):
        try:
            analysis.save_segments(os.path.join(self.run_dir, "segments.json"),
                                   self.substrate, self.segments)
        except OSError as err:
            logger.error(f"Could not save segments.json: {err}")

    def _append_result_csv(self, seg, r):
        path = os.path.join(self.run_dir, "Analysis.csv")
        try:
            write_header = not self._csv_header_written and not os.path.exists(path)
            with open(path, "a", newline="") as f:
                w = csv.writer(f)
                if write_header:
                    w.writerow(["Time (s)", "Segment", "Material", "Thickness (nm)",
                                "Rate (um/hr)", "Rate unc (um/hr)",
                                "Roughness sigma (nm)", "Flags"])
                w.writerow([f"{r.t:.2f}", seg.kind, seg.material, f"{r.d_nm:.2f}",
                            f"{r.rate_um_hr:.3f}", f"{r.rate_unc_um_hr:.3f}",
                            f"{r.sigma_nm:.2f}", "|".join(r.flags)])
            self._csv_header_written = True
        except OSError as err:
            logger.error(f"Could not append Analysis.csv: {err}")


class AnalysisPanel(QWidget):
    """The collapsible analysis panel. Create it lazily on first expand."""

    def __init__(self, run_dir, plot_widget, pyro=None, parent=None):
        super().__init__(parent)
        self.controller = AnalysisController(run_dir)
        self.pyro = pyro  # pyrometer.LivePyrometer from the main window, or None
        self.plot = plot_widget
        self.marker_items = []
        self.region_items = []
        self.model_curve = plot_widget.plot([], [], pen=pg.mkPen((153, 0, 0), width=2))

        grid = QGridLayout(self)
        grid.setContentsMargins(4, 2, 4, 2)

        self.substrate_box = QComboBox()
        self.substrate_box.addItems(list(materials.SUBSTRATES))
        self.substrate_box.currentTextChanged.connect(
            lambda name: self.controller.command("substrate", name))
        self.temp_spin = QDoubleSpinBox()
        self.temp_spin.setRange(0, 1500)
        self.temp_spin.setValue(1050)
        self.temp_spin.setSuffix(" C")
        self.calibrate_btn = QPushButton("Calibrate R")
        self.calibrate_btn.clicked.connect(
            lambda: self.controller.command("calibrate", self.temp_spin.value()))
        self.pyro_btn = QPushButton("Set T ref")
        self.pyro_btn.setToolTip("Re-pin the pyrometer scale for this run so the current signal reads "
                                 "the temperature in the box (e.g. the TC on a steady plateau).\n"
                                 "Leave it alone to keep the fixed S432 scale, which is comparable between runs.")
        self.pyro_btn.clicked.connect(self._set_pyro_reference)
        self.leak_btn = QPushButton("Cal leak")
        self.leak_btn.setToolTip("Measure the laser leak ratio g from the last 30 s.\n"
                                 "Use only cold (below ~400 C) with the laser on and the beam on the detector.")
        self.leak_btn.clicked.connect(self._calibrate_leak)
        if pyro is None:
            self.pyro_btn.setEnabled(False)
            self.leak_btn.setEnabled(False)

        self.material_box = QComboBox()
        self.material_box.addItems(list(materials.FILMS))
        self.dep_start_btn = QPushButton("Dep start")
        self.dep_start_btn.clicked.connect(
            lambda: self.controller.command("dep_start", self.material_box.currentText(),
                                            self.temp_spin.value()))
        self.dep_end_btn = QPushButton("Dep end")
        self.dep_end_btn.clicked.connect(lambda: self.controller.command("dep_end"))
        self.etch_start_btn = QPushButton("Etch start")
        self.etch_start_btn.clicked.connect(
            lambda: self.controller.command("etch_start", self.material_box.currentText(),
                                            self.temp_spin.value()))
        self.etch_end_btn = QPushButton("Etch end")
        self.etch_end_btn.clicked.connect(lambda: self.controller.command("etch_end"))

        self.thickness_label = QLabel("Thickness: -")
        self.rate_label = QLabel("Rate: -")
        self.rough_label = QLabel("Roughness: -")
        self.temp_label = QLabel("T: -")
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: gray")

        grid.addWidget(QLabel("Substrate:"), 0, 0)
        grid.addWidget(self.substrate_box, 0, 1)
        grid.addWidget(self.temp_spin, 0, 2)
        grid.addWidget(self.calibrate_btn, 0, 3)
        grid.addWidget(self.pyro_btn, 0, 4)
        grid.addWidget(self.leak_btn, 0, 5)
        grid.addWidget(QLabel("Material:"), 1, 0)
        grid.addWidget(self.material_box, 1, 1)
        grid.addWidget(self.dep_start_btn, 1, 2)
        grid.addWidget(self.dep_end_btn, 1, 3)
        grid.addWidget(self.etch_start_btn, 1, 4)
        grid.addWidget(self.etch_end_btn, 1, 5)
        grid.addWidget(self.thickness_label, 2, 0, 1, 2)
        grid.addWidget(self.rate_label, 2, 2, 1, 2)
        grid.addWidget(self.rough_label, 2, 4)
        grid.addWidget(self.temp_label, 2, 5)
        grid.addWidget(self.status_label, 3, 0, 1, 6)

    # ---- pyrometer controls (GUI thread, like the pyrometer itself) --------
    def _set_pyro_reference(self):
        msg = self.pyro.set_reference(self.temp_spin.value())
        logger.info(msg)
        self.controller.set_status(msg)

    def _calibrate_leak(self):
        msg = self.pyro.calibrate_leak()
        logger.info(msg)
        self.controller.set_status(msg)

    # ---- data path ---------------------------------------------------------
    def feed(self, t, v, ch2):
        self.controller.feed(t, v, ch2)

    def refresh(self):
        snap = self.controller.snapshot()
        r = snap["latest"]
        if r is not None:
            frozen = " (frozen)" if "D_FROZEN" in r.flags else ""
            self.thickness_label.setText(f"Thickness: {r.d_nm:.0f} nm{frozen}")
            if np.isnan(r.rate_nm_s):
                self.rate_label.setText("Rate: - (post-onset)")
            elif "RATE_UNCERTAIN" in r.flags:
                self.rate_label.setText(f"Rate: {r.rate_um_hr:.2f} um/hr (UNCERTAIN)")
            else:
                self.rate_label.setText(
                    f"Rate: {r.rate_um_hr:.2f} ± {r.rate_unc_um_hr:.2f} um/hr")
            if "ROUGHENING" in r.flags or "POST_ONSET" in r.flags:
                self.rough_label.setText(f"Roughness: {r.sigma_nm:.0f} nm ⚠ ROUGH")
                self.rough_label.setStyleSheet("color: rgb(200,40,0); font-weight: bold")
                self.rough_label.setToolTip("Roughening confirmed: specular envelope has collapsed")
            elif "ENVELOPE_DECAY" in r.flags:
                self.rough_label.setText(f"Roughness: {r.sigma_nm:.0f} nm ⚠ decay")
                self.rough_label.setStyleSheet("color: rgb(190,130,0); font-weight: bold")
                self.rough_label.setToolTip("Warning: fringe peaks are decaying faster than normal")
            else:
                self.rough_label.setText(f"Roughness: {r.sigma_nm:.0f} nm")
                self.rough_label.setStyleSheet("")
                self.rough_label.setToolTip("")
        if self.pyro is not None and self.pyro.last is not None:
            T = self.pyro.last[5]
            self.temp_label.setText(f"T: {T:.1f} C" if np.isfinite(T) else "T: below range")
            self.temp_label.setToolTip(self.pyro.describe())
        self.status_label.setText(snap["status"])

        tm, vm = snap["model"]
        if len(tm):
            self.model_curve.setData(tm, vm)
        self._draw_segments(snap["segments"])

    def _draw_segments(self, segments):
        # rebuild markers only when the segment count or open-state changes
        state = (len(segments), segments[-1].t_end if segments else None)
        if getattr(self, "_seg_state", None) == state:
            return
        self._seg_state = state
        for item in self.marker_items + self.region_items:
            self.plot.removeItem(item)
        self.marker_items, self.region_items = [], []
        for seg in segments:
            line = pg.InfiniteLine(pos=seg.t_start, angle=90,
                                   pen=pg.mkPen(MARKER_PEN, style=QtCore.Qt.DashLine))
            self.plot.addItem(line)
            self.marker_items.append(line)
            end = seg.t_end
            if end is not None:
                line2 = pg.InfiniteLine(pos=end, angle=90,
                                        pen=pg.mkPen(MARKER_PEN, style=QtCore.Qt.DashLine))
                self.plot.addItem(line2)
                self.marker_items.append(line2)
                brush = DEP_BRUSH if seg.kind == "deposition" else ETCH_BRUSH
                region = pg.LinearRegionItem(values=(seg.t_start, end), movable=False,
                                             brush=pg.mkBrush(*brush))
                region.setZValue(-10)
                self.plot.addItem(region)
                self.region_items.append(region)

    def shutdown(self):
        self.controller.stop()
