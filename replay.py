# replay.py
#
# Replay data source: feeds a recorded Reflectance.csv through the live GUI
# pipeline as if the instrument were producing it, at an adjustable speed.
#
# Each recorded point is expanded into a synthetic 512 Hz burst whose upper
# median reproduces the recorded reflectance (the real bursts are a rotation
# wobble - dips below the best-aligned level - so the synthetic burst dips
# downward from the recorded value). Timestamps are the RECORDED times, only
# the wall-clock delivery is accelerated, so analysis sees real seconds and
# rates come out in real units at any replay speed.
#
# Usage: set RTLR_REPLAY_FILE (and optionally RTLR_REPLAY_SPEED) in common.py,
# or pass a path on the command line: python rtlr.py <recorded.csv> [speed]
#
# David Lister
# August 2026

import csv
import time
import logging
import threading

import numpy as np

import common

logger = logging.getLogger("RTLR.replay")


class ReplayHandler:
    """Drop-in replacement for srs830.SRS830Handler that replays a recording."""

    def __init__(self, data_queue, command_queue, run_name, run_dir,
                 replay_file=None, speed=20.0):
        self.queue_data_out = data_queue
        self.queue_commands_in = command_queue  # accepted for interface parity
        self.replay_file = replay_file
        self.speed = max(speed, 0.01)
        self.event_end = threading.Event()
        self.connected_port = None  # interface parity with SRS830Handler
        self.status = f"Replaying {replay_file} at {self.speed:g}x"
        self.init_wall = time.time()
        self.rng = np.random.default_rng(1)

        self.rows = []   # (t_recorded, reflectance, ch2 or None)
        with open(replay_file, encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            header = next(reader)
            has_ch2 = len(header) >= 3
            for row in reader:
                try:
                    t, v = float(row[0]), float(row[1])
                except (ValueError, IndexError):
                    continue
                if not (np.isfinite(t) and np.isfinite(v)):
                    continue
                ch2 = float(row[2]) if has_ch2 and len(row) > 2 else 0.0
                self.rows.append((t, v, ch2))
        logger.info(f"Replay loaded {len(self.rows)} points from {replay_file} "
                    f"at {self.speed:.0f}x speed")

        self.p = threading.Thread(target=self.run, daemon=True)
        self.p.start()

    def _synth_burst(self, value, ch2):
        n = 464
        timebase = np.arange(n) / common.SRS830_CAPTURE_RATE_HZ
        # rotation-wobble ripple: dips below the best-aligned (recorded) level
        saw = np.sin(2 * np.pi * 8.5 * timebase + self.rng.uniform(0, 2 * np.pi))
        dip = 0.30 * np.clip(-saw, 0.0, None) ** 2
        data_r = value * (1.0 - dip) + self.rng.normal(0, 0.003 * max(value, 1e-6), n)
        data_ch2 = np.full(n, ch2)
        return timebase, data_r, data_ch2

    def run(self):
        if not self.rows:
            logger.error("Replay file had no usable rows")
            return
        t0 = self.rows[0][0]
        start_wall = time.time()
        for t_rec, v, ch2 in self.rows:
            wall_due = start_wall + (t_rec - t0) / self.speed
            delay = wall_due - time.time()
            if delay > 0 and self.event_end.wait(delay):
                break
            if self.event_end.is_set():
                break
            burst = self._synth_burst(v, ch2)
            # recorded timestamp, mapped so MainWindow's (t - init_time) = t_rec
            self.queue_data_out.put([self.init_wall + t_rec, burst])
        self.status = "Replay finished"
        logger.info("Replay finished")

    def stop(self):
        self.event_end.set()

    def join(self, timeout=None):
        self.p.join(timeout)
