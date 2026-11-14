# pyrometer.py
#
# Temperature from the CH2 (Aux In 3) photodiode DC signal.
#
# CH2 = a + g * R_mean + thermal. The laser's own DC light leaks into CH2 in
# proportion to the lock-in R (g ~ 6.4 at SLVL 1), and the dark offset a is a
# fraction of a mV. The 650 nm bandpass does not block the near-IR, so the
# thermal part is ~1 um light and follows Wien at an effective wavelength of
# ~1.21 um (fitted on S432 against the thermocouple). The 650 nm reflectance
# therefore cannot correct the emissivity, and none is applied: on sapphire
# the reading is dominated by the susceptor seen through the wafer.
#
#     S = CH2 - a - g * R_mean
#     1/T = 1/T_ref - (lambda_eff / c2) * ln(S / S_ref)
#
# The default reference (common.PYRO_REF_*) is fixed, so every run is reported
# on the same (S432) scale and bake plateaus can be compared between runs.
#
# David Lister
# November 2026

import math
from collections import deque

import numpy as np

import common

C2_M_K = 0.014388


def wien_temperature_c(signal_v, ref_signal_v, ref_temp_c,
                       lambda_eff_um=common.PYRO_LAMBDA_EFF_UM):
    """Temperature (C) for a thermal signal, relative to a reference point.
    NaN for a non-positive signal or reference."""
    if not (signal_v > 0 and ref_signal_v > 0):
        return float("nan")
    inv = 1.0 / (ref_temp_c + 273.15) - (lambda_eff_um * 1e-6 / C2_M_K) * math.log(signal_v / ref_signal_v)
    if inv <= 0:
        return float("nan")
    return 1.0 / inv - 273.15


class LivePyrometer:
    """Turns 1 s (R_mean, CH2) blocks into temperatures, tracking the dark
    offset (laser-off blocks while cold) and the leak ratio (cold start or a
    manual calibration). Single-threaded: call it from the GUI thread."""

    def __init__(self, g=common.PYRO_LEAK_G, dark_v=common.PYRO_DARK_V,
                 ref_signal_v=common.PYRO_REF_SIGNAL_V, ref_temp_c=common.PYRO_REF_TEMP_C,
                 lambda_eff_um=common.PYRO_LAMBDA_EFF_UM, auto_leak_s=common.PYRO_AUTO_LEAK_S):
        self.g = g
        self.a = dark_v
        self.g_source = "default (S432)"
        self.a_source = "default (S432)"
        self.ref_signal_v = ref_signal_v
        self.ref_temp_c = ref_temp_c
        self.ref_label = "S432 scale"
        self.lambda_eff_um = lambda_eff_um
        self.auto_leak_s = auto_leak_s
        self._auto_ratios = []
        self.auto_leak_active = auto_leak_s > 0
        self._dark = deque(maxlen=600)
        self.history = deque(maxlen=600)   # (t, laser_on, r_mean, ch2, S, T)
        self.last = None

    # ---- per-block update ----------------------------------------------------
    def update(self, t, r_mean, ch2):
        """Process one block; returns its temperature (C) or NaN."""
        laser_on = r_mean >= common.PYRO_LASER_OFF_R_V
        if not laser_on:
            # Laser off / beam blocked: CH2 is dark + thermal. While cold it
            # gives the dark level; while hot it is a leak-free thermal reading.
            if abs(ch2 - self.a) < common.PYRO_DARK_MAX_V:
                self._dark.append(ch2)
                self.a = float(np.median(self._dark))
                self.a_source = f"laser off ({len(self._dark)} s)"
            S = ch2 - self.a
        else:
            if self.auto_leak_active:
                self._auto_leak_step(r_mean, ch2)
            S = ch2 - self.a - self.g * r_mean
        T = self.temperature(S)
        self.last = (t, laser_on, r_mean, ch2, S, T)
        self.history.append(self.last)
        return T

    def _auto_leak_step(self, r_mean, ch2):
        if ch2 - self.a - self.g * r_mean > common.PYRO_COLD_MAX_V:
            # already warm (or the default g is far off): keep what we have
            self.auto_leak_active = False
            if len(self._auto_ratios) >= 10:
                self._set_g(float(np.median(self._auto_ratios)),
                            f"cold start ({len(self._auto_ratios)} s)")
            return
        self._auto_ratios.append((ch2 - self.a) / r_mean)
        if len(self._auto_ratios) >= self.auto_leak_s:
            self.auto_leak_active = False
            self._set_g(float(np.median(self._auto_ratios)),
                        f"cold start ({len(self._auto_ratios)} s)")

    def _set_g(self, g, source):
        if np.isfinite(g) and g > 0:
            self.g, self.g_source = g, source

    def temperature(self, S):
        if not S > common.PYRO_MIN_SIGNAL_V:
            return float("nan")
        return wien_temperature_c(S, self.ref_signal_v, self.ref_temp_c, self.lambda_eff_um)

    # ---- operator actions ------------------------------------------------------
    def calibrate_leak(self, seconds=30):
        """g from the last `seconds` laser-on blocks. Only meaningful cold
        (below ~400 C) with the laser on. Returns a status message."""
        rows = [h for h in list(self.history)[-seconds:] if h[1]]
        if len(rows) < 5:
            return "Leak calibration needs at least 5 s of laser-on data"
        g = float(np.median([(h[3] - self.a) / h[2] for h in rows]))
        if not (np.isfinite(g) and g > 0):
            return f"Leak calibration failed (g = {g:.3g})"
        self.auto_leak_active = False
        self._set_g(g, f"manual ({len(rows)} s)")
        return f"Leak ratio g = {g:.2f} (CH2 per V of R), dark a = {1e3 * self.a:.2f} mV"

    def set_reference(self, temp_c, seconds=10):
        """Pin the scale so the current thermal signal reads temp_c."""
        rows = [h for h in list(self.history)[-seconds:]]
        if len(rows) < 3:
            return "Not enough data to set the T reference yet"
        S = float(np.median([h[4] for h in rows]))
        if not S > common.PYRO_MIN_SIGNAL_V:
            return (f"T reference needs a thermal signal above {1e3 * common.PYRO_MIN_SIGNAL_V:.0f} mV "
                    f"(got {1e3 * S:.1f} mV): too cold, or CH2 not on the pyrometer input")
        self.ref_signal_v, self.ref_temp_c = S, float(temp_c)
        self.ref_label = f"ref {temp_c:.0f} C at t={rows[-1][0]:.0f} s"
        return f"T reference set: {temp_c:.0f} C at S = {S:.4f} V"

    def reset_reference(self):
        self.ref_signal_v, self.ref_temp_c = common.PYRO_REF_SIGNAL_V, common.PYRO_REF_TEMP_C
        self.ref_label = "S432 scale"

    # ---- readouts ------------------------------------------------------------
    def window_stats(self, seconds=60):
        """(mean, sd, n) of the finite temperatures in the last `seconds` blocks."""
        if not self.history:
            return float("nan"), float("nan"), 0
        t_end = self.history[-1][0]
        T = np.array([h[5] for h in self.history if h[0] > t_end - seconds and np.isfinite(h[5])])
        if T.size == 0:
            return float("nan"), float("nan"), 0
        return float(T.mean()), float(T.std()), int(T.size)

    def describe(self):
        return (f"S = CH2 - a - g*R; a = {1e3 * self.a:.2f} mV [{self.a_source}], "
                f"g = {self.g:.2f} [{self.g_source}], lambda_eff = {self.lambda_eff_um:.2f} um, "
                f"{self.ref_label} (S {self.ref_signal_v:.3f} V = {self.ref_temp_c:.1f} C)")


CSV_HEADER = "Time (s),R mean (V),CH2 (V),Laser on,Thermal S (V),T pyro (C),a (V),g\n"


def csv_row(p, t):
    _, laser_on, r_mean, ch2, S, T = p.last
    T_txt = f"{T:.2f}" if np.isfinite(T) else ""
    return f"{t},{r_mean:.6g},{ch2:.6g},{int(laser_on)},{S:.6g},{T_txt},{p.a:.6g},{p.g:.4f}\n"
