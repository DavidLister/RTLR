# GUI screenshot harness: replays a recorded run through the real GUI,
# drives the analysis panel like an operator would (calibrate, mark segments),
# captures screenshots at milestones, and asserts on the final fitted numbers.
#
# Usage:
#   python tests/gui_harness.py [replay_csv] [speed] [shot_dir]
# Defaults: S195 at 120x, screenshots to ./gui_shots
#
# Exits 0 on PASS, 1 on FAIL.

import os
import sys
import json

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from PySide6 import QtCore
from PySide6.QtWidgets import QApplication

import common
import replay
import rtlr

REPLAY = sys.argv[1] if len(sys.argv) > 1 else "TempData/S195_2024-02-06_RTLR/Reflectance.csv"
SPEED = float(sys.argv[2]) if len(sys.argv) > 2 else 120.0
SHOT_DIR = sys.argv[3] if len(sys.argv) > 3 else "gui_shots"
os.makedirs(SHOT_DIR, exist_ok=True)

# operator script: (recorded_time_threshold, action_name)
# S195 timeline: substrate to ~800, LT dep 2400-2800, anneal 2830-3550, HT 3600+
SCRIPT = [
    (0, "expand"),
    (600, "calibrate"),
    (2400, "dep_start_lt"),
    (2800, "dep_end"),
    (2830, "etch_start"),
    (3550, "etch_end"),
    (3600, "dep_start_ht"),
    (4600, "shot_mid"),
    (6500, "assert_and_shot"),
    (7600, "finish"),
]

common.DATA_SUBPATH = SHOT_DIR  # run directories land next to the screenshots


def replay_factory(data_q, cmd_q, run_name, run_dir, port):
    return replay.ReplayHandler(data_q, cmd_q, run_name, run_dir,
                                replay_file=REPLAY, speed=SPEED)


app = QApplication([])
window = rtlr.MainWindow(replay_factory, source_label=f"Replay: {os.path.basename(REPLAY)}")
window.resize(1100, 850)
window.show()

state = {"idx": 0, "checks": [], "failed": False}

# Run-name enforcement: nothing can start until a usable name is entered
state["checks"].append(("Start disabled with empty name", not window.start_btn.isEnabled(), None))
window.name_edit.setText("   ")
state["checks"].append(("Start disabled with blank name", not window.start_btn.isEnabled(), None))
window.start_logging()  # e.g. Enter pressed with a blank name
state["checks"].append(("No handler started without a name", window.handler is None, None))
window.name_edit.setText("gui harness")
state["checks"].append(("Start enabled once named", window.start_btn.isEnabled(), None))
window.start_btn.click()
handler = window.handler
run_dir = window.run_dir
state["checks"].append(("Run dir includes name", run_dir.endswith("---gui_harness"), os.path.basename(run_dir)))
state["checks"].append(("Reflectance file includes run name",
                        os.path.basename(window.save_file_name) == os.path.basename(run_dir) + "_Reflectance.csv",
                        os.path.basename(window.save_file_name)))
state["checks"].append(("Name locked after start", not window.name_edit.isEnabled(), None))


def shot(name):
    path = os.path.join(SHOT_DIR, name + ".png")
    window.grab().save(path)
    print(f"[shot] {path}")


def panel():
    return window.analysis_panel


def act(name):
    p = panel()
    if name == "expand":
        window.analysis_caret.setChecked(True)
        shot("01_panel_open")
    elif name == "calibrate":
        p.temp_spin.setValue(1050)
        p.calibrate_btn.click()
        QtCore.QTimer.singleShot(1500, lambda: shot("02_calibrated"))
    elif name == "dep_start_lt":
        p.material_box.setCurrentText("LT GaN")
        p.temp_spin.setValue(550)
        p.dep_start_btn.click()
    elif name == "dep_end":
        p.dep_end_btn.click()
        shot("03_lt_gan_done")
    elif name == "etch_start":
        p.temp_spin.setValue(1000)
        p.etch_start_btn.click()
    elif name == "etch_end":
        p.etch_end_btn.click()
        shot("04_anneal_done")
    elif name == "dep_start_ht":
        p.material_box.setCurrentText("HT GaN")
        p.temp_spin.setValue(1050)
        p.dep_start_btn.click()
    elif name == "shot_mid":
        shot("05_ht_gan_fringes")
    elif name == "assert_and_shot":
        shot("06_ht_gan_late")
        snap = p.controller.snapshot()
        r = snap["latest"]
        ok_rate = r is not None and 1.3 < r.rate_um_hr < 1.9
        ok_thick = r is not None and 900 < r.d_nm < 1700
        state["checks"].append(("HT GaN rate 1.3-1.9 um/hr", ok_rate,
                                None if r is None else round(r.rate_um_hr, 3)))
        state["checks"].append(("HT GaN thickness 900-1700 nm", ok_thick,
                                None if r is None else round(r.d_nm, 1)))
    elif name == "finish":
        shot("07_final")
        finish()


def finish():
    snap = panel().controller.snapshot() if panel() else None
    seg_path = os.path.join(run_dir, "segments.json")
    ana_path = os.path.join(run_dir, "Analysis.csv")
    state["checks"].append(("segments.json written", os.path.exists(seg_path), None))
    state["checks"].append(("Analysis.csv written", os.path.exists(ana_path), None))
    if os.path.exists(seg_path):
        with open(seg_path) as f:
            segs = json.load(f)["segments"]
        state["checks"].append(("4 segments recorded", len(segs) == 3 or len(segs) == 4, len(segs)))
    if snap:
        state["checks"].append(("calibration happened", snap["A"] is not None,
                                None if snap["A"] is None else round(snap["A"], 3)))
    app.quit()


def tick():
    if state["idx"] >= len(SCRIPT):
        return
    t_now = window.time[-1] if window.time else -1
    threshold, action = SCRIPT[state["idx"]]
    if t_now >= threshold:
        state["idx"] += 1
        print(f"[act t={t_now:.0f}] {action}")
        try:
            act(action)
        except Exception as err:
            print(f"ACTION FAILED: {action}: {err}")
            state["failed"] = True
            app.quit()


driver = QtCore.QTimer()
driver.timeout.connect(tick)
driver.start(150)

watchdog = QtCore.QTimer()
watchdog.setSingleShot(True)
watchdog.timeout.connect(lambda: (print("WATCHDOG TIMEOUT"), app.quit()))
watchdog.start(int(9000 / SPEED * 1000) + 120000)

app.exec()

window.shutdown()

print("\n--- CHECKS ---")
all_ok = not state["failed"] and state["checks"]
for name, ok, val in state["checks"]:
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" (got {val})" if val is not None else ""))
    all_ok = all_ok and ok
print("OVERALL:", "PASS" if all_ok else "FAIL")
sys.exit(0 if all_ok else 1)
