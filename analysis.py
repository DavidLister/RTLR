# analysis.py
#
# Reflectance analysis engine for RTLR: virtual-interface (Breiland & Killeen,
# JAP 78, 6726 (1995)) sliding-window fitting of in-situ laser reflectometry.
#
# Core ideas:
#   - The buried stack under the growing film is one complex field reflectance
#     r_v; per segment we fit growth rate G, r_v, and two distinct damping
#     mechanisms: top-surface roughness (Debye-Waller factor on the whole
#     specular signal - drops the mean, the "roughening alarm") and thickness
#     non-uniformity across the laser spot (damps fringe contrast only -
#     benign, modeled by Gaussian averaging over d).
#   - Thickness is never inverted pointwise from R (multi-valued); it is
#     propagated: d = 0 at dep start, d(t) integrates the fitted rate, and
#     every window fit is warm-started from the previous one so the fit can
#     never jump interference branches.
#   - Parameters unlock progressively as the segment accumulates fringe phase:
#     sub-fringe windows can only support G; ~a fringe supports r_v and the
#     damping terms; multiple fringes can support k (absorption).
#
# Works offline (CLI: python analysis.py <run_dir> --segments segments.json)
# and as the live engine behind the GUI analysis panel.
#
# David Lister
# August 2026

import os
import csv
import json
import logging
import argparse
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import least_squares
from scipy.signal import find_peaks

import materials

logger = logging.getLogger("RTLR.analysis")

C2_M_K = 1.4388e-2          # Second radiation constant (m*K)
K_B_EV = 8.617333e-5        # Boltzmann constant (eV/K)

# Parameter-unlock thresholds, in accumulated fringe phase (fractions of a
# full fringe spanned by the fit window). Roughness sigma is always free -
# it sets the absolute signal level, which is identifiable from the first
# point once the scale is calibrated (and 3D-recovery starts NEED it free).
SPAN_UNLOCK_RV = 0.35
SPAN_UNLOCK_DAMPING = 1.1
SPAN_UNLOCK_K = 2.5

# Floor used when converting window time-span to fringe span, so a collapsed
# rate estimate can never permanently lock the parameter schedule
SPAN_RATE_FLOOR_NM_S = 0.05

# Multi-start rate candidates for re-initialization when a fit is stuck.
# Re-init is expensive (grid of full fits) so it runs at most once per
# REINIT_COOLDOWN_POINTS unless it actually improved the fit.
RATE_REINIT_GRID_NM_S = [0.05, 0.1, 0.2, 0.3, 0.45, 0.65, 1.0, 1.5]
REINIT_COOLDOWN_POINTS = 15

MAX_WINDOW_POINTS = 600
MIN_FIT_POINTS = 8
DEFAULT_RATE_GUESS_NM_S = {"deposition": 0.4, "etch": -0.005}

# Roughening-onset heuristics, deliberately fit-independent: fitted sigma
# also absorbs benign scale losses (alignment/bowing drift) and its split
# against the contrast-damping term is not stable enough to alarm on.
# Detection tracks EWMA envelopes (time constant ~1 fringe) of the signal
# mean and oscillation amplitude. Roughening = fringe amplitude has halved
# from its running maximum AND the mean sits well below where it was when
# fringes were strongest AND the mean is still falling. Anchoring the mean
# comparison at the amplitude maximum makes coalescence transients (which
# have large amplitude but a RISING mean afterwards) immune to false alarms.
ROUGH_AMP_FRACTION = 0.5          # amplitude vs its running max
ROUGH_MEAN_FRACTION = 0.85        # mean vs mean at amplitude max
ROUGHENING_PERSIST_POINTS = 3     # consecutive detections before flagging

# Early soft warning (ENVELOPE_DECAY): three consecutive fringe peaks, each
# at least this much below the previous. Fires ~1-2 fringes before the
# confirmed ROUGHENING alarm; healthy runs decline ~1-3% per fringe.
PEAK_DECAY_RATIO = 0.90
SUBSTRATE_CROSS_FRACTION = 0.93  # signal below this fraction of the bare-substrate level

# Rate is only identifiable while the window contains visible fringe contrast
MIN_CONTRAST_OVER_NOISE = 4.0

GH_NODES, GH_WEIGHTS = np.polynomial.hermite.hermgauss(5)


# ---------------------------------------------------------------------------
# Data loading

@dataclass
class RunData:
    t: np.ndarray
    refl: np.ndarray
    ch2: np.ndarray | None
    path: str
    n_dropped: int = 0


def load_run(path):
    """Load a Reflectance.csv (or a run directory containing one). NaN rows dropped."""
    if os.path.isdir(path):
        path = os.path.join(path, "Reflectance.csv")
    t, refl, ch2 = [], [], []
    has_ch2 = False
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader)
        has_ch2 = len(header) >= 3
        for row in reader:
            if len(row) < 2:
                continue
            t.append(float(row[0]))
            refl.append(float(row[1]))
            if has_ch2:
                ch2.append(float(row[2]))
    t = np.array(t)
    refl = np.array(refl)
    ch2 = np.array(ch2) if has_ch2 else None
    good = np.isfinite(t) & np.isfinite(refl)
    n_dropped = int((~good).sum())
    if n_dropped:
        logger.warning(f"Dropped {n_dropped} non-finite rows from {path}")
    return RunData(t[good], refl[good], ch2[good] if has_ch2 else None, path, n_dropped)


# ---------------------------------------------------------------------------
# Segments

@dataclass
class Segment:
    kind: str                    # "substrate" | "deposition" | "etch"
    t_start: float
    t_end: float | None = None   # None = open (live)
    material: str | None = None  # film name for deposition/etch
    temp_c: float | None = None

    def to_dict(self):
        return {"kind": self.kind, "t_start": self.t_start, "t_end": self.t_end,
                "material": self.material, "temp_c": self.temp_c}


def load_segments(path):
    with open(path) as f:
        data = json.load(f)
    subs = data.get("substrate", "Al2O3")
    segs = [Segment(**s) for s in data["segments"]]
    return subs, segs


def save_segments(path, substrate, segments):
    with open(path, "w") as f:
        json.dump({"substrate": substrate,
                   "segments": [s.to_dict() for s in segments]}, f, indent=2)


# ---------------------------------------------------------------------------
# Physics

def _noise_estimate(v):
    """Point noise from second differences - insensitive to fringe slope."""
    if len(v) < 3:
        return 1e-9
    return float(np.median(np.abs(np.diff(v, n=2)))) / np.sqrt(6.0) + 1e-9


def fresnel_r(N1, N2):
    return (N1 - N2) / (N1 + N2)


def vi_field_reflectance(d_nm, N_film, r_v, wavelength_nm):
    """Field reflectance of (ambient | film of thickness d | virtual interface r_v)."""
    r1 = fresnel_r(1.0 + 0j, N_film)
    beta = 2.0 * np.pi * N_film * d_nm / wavelength_nm
    ph = np.exp(2j * beta)
    return (r1 + r_v * ph) / (1.0 + r1 * r_v * ph)


def model_reflectance(d_nm, N_film, r_v, sigma_nm, dd_nm, wavelength_nm):
    """Specular reflectance including roughness (Debye-Waller on everything)
    and thickness non-uniformity (Gauss-Hermite average over d, damps fringe
    contrast only). d_nm may be an array; sigma_nm may be a scalar or an
    array matching d_nm (time-varying roughness within a fit window)."""
    d_nm = np.atleast_1d(np.asarray(d_nm, dtype=float))
    R = np.zeros(d_nm.shape)
    norm = GH_WEIGHTS.sum()
    for x, w in zip(GH_NODES, GH_WEIGHTS):
        di = d_nm + np.sqrt(2.0) * dd_nm * x
        r = vi_field_reflectance(di, N_film, r_v, wavelength_nm)
        R += (w / norm) * np.real(r * np.conj(r))
    sigma = np.clip(np.asarray(sigma_nm, dtype=float), 0.0, None)
    dw = np.exp(-((4.0 * np.pi * sigma / wavelength_nm) ** 2))
    return R * dw


def bare_substrate_reflectance(substrate_name, temp_c=None):
    N = materials.SUBSTRATES[substrate_name].nk(temp_c)
    r = fresnel_r(1.0 + 0j, N)
    return float(np.real(r * np.conj(r)))


# ---------------------------------------------------------------------------
# Per-segment sliding-window fitter

@dataclass
class FitResult:
    t: float
    d_nm: float
    rate_nm_s: float
    rate_unc_nm_s: float
    sigma_nm: float
    dd_nm: float
    k: float
    r_v: complex
    resid_rms: float
    span_fringes: float
    flags: list = field(default_factory=list)

    @property
    def rate_um_hr(self):
        return self.rate_nm_s * 3.6

    @property
    def rate_unc_um_hr(self):
        return self.rate_unc_nm_s * 3.6


class SegmentAnalyzer:
    """Sliding-window virtual-interface fit for one deposition or etch segment.

    Feed points with add_point(); returns a FitResult once enough data exists.
    """

    def __init__(self, segment, scale_A, substrate_name,
                 d0_nm=0.0, r_v_init=None, wavelength_nm=materials.LASER_WAVELENGTH_NM,
                 allow_k_fit=False, start_rough=False):
        self.segment = segment
        self.material = materials.FILMS[segment.material]
        self.N_film = self.material.nk(segment.temp_c)
        self.wavelength = wavelength_nm
        self.A = scale_A
        self.substrate_name = substrate_name
        self.allow_k_fit = allow_k_fit

        self.d0 = d0_nm                  # thickness at segment start
        if r_v_init is None:
            N_sub = materials.SUBSTRATES[substrate_name].nk(segment.temp_c)
            r_v_init = fresnel_r(self.N_film, N_sub)
        self.r_v = complex(r_v_init)

        kind = segment.kind
        self.rate = DEFAULT_RATE_GUESS_NM_S.get(kind, 0.4)
        self.start_rough = start_rough
        self.sigma = 0.0
        self.sigma_slope = 0.0           # nm/s, roughness evolution within a window
        self.dd = 0.0
        self.k = self.N_film.imag
        self.d_start_window = self.d0    # thickness at the first point of the current window
        self.r_v_init = self.r_v
        self._wide_anchor = False        # widened d_start bounds for re-init trials
        self._sigma_initialized = not start_rough
        self._reinit_block_until = 0     # results-count gate for multi-start
        self._uncertain_streak = 0       # consecutive RATE_UNCERTAIN results
        self._roughening_streak = 0      # consecutive envelope-decline detections
        self._env_mean = None            # EWMA envelope state for the detector
        self._env_amp = 0.0
        self._env_amp_max = 0.0
        self._env_mean_at_amp_max = None
        self._env_fringe_ref = None      # fringe clock frozen at amplitude max
        self._env_hist = []              # (t, env_mean) history for the falling test

        self.t = []
        self.v = []
        self.window_start_idx = 0
        self.results = []
        self.roughening_onset_t = None
        self.substrate_level_v = scale_A * bare_substrate_reflectance(substrate_name, segment.temp_c)

    def _sigma_free(self, span):
        return self.start_rough or span >= SPAN_UNLOCK_DAMPING

    # -- parameter vector helpers ------------------------------------------
    def _pack(self, span):
        p = [self.rate, self.d_start_window]
        self._free = ["rate", "d_start"]
        if self._sigma_free(span):
            p += [self.sigma, self.sigma_slope]
            self._free += ["sigma", "sigma_slope"]
        if span >= SPAN_UNLOCK_RV:
            p += [self.r_v.real, self.r_v.imag]
            self._free += ["rv_re", "rv_im"]
        if span >= SPAN_UNLOCK_DAMPING:
            p += [self.dd]
            self._free += ["dd"]
        if span >= SPAN_UNLOCK_K and self.allow_k_fit:
            p += [self.k]
            self._free += ["k"]
        return np.array(p)

    def _unpack(self, p):
        vals = dict(zip(self._free, p))
        rate = vals["rate"]
        d_start = vals["d_start"]
        rv = complex(vals.get("rv_re", self.r_v.real), vals.get("rv_im", self.r_v.imag))
        sigma = vals.get("sigma", self.sigma)
        sigma_slope = vals.get("sigma_slope", self.sigma_slope)
        dd = vals.get("dd", self.dd)
        k = vals.get("k", self.k)
        return rate, d_start, rv, sigma, sigma_slope, dd, k

    def _bounds(self):
        d_slack = 0.5 * self.wavelength / (2.0 * self.N_film.real) if self._wide_anchor else 15.0
        lo, hi = [], []
        for name in self._free:
            b = {
                "rate": (-0.6, 0.1) if self.segment.kind == "etch" else (0.0, 6.0),
                "d_start": (self.d_start_window - d_slack, self.d_start_window + d_slack),
                "rv_re": (-0.999, 0.999),
                "rv_im": (-0.999, 0.999),
                "sigma": (0.0, 60.0),
                "sigma_slope": (-0.3, 0.3),
                "dd": (0.0, 120.0),
                "k": (0.0, 0.05),
            }[name]
            lo.append(b[0])
            hi.append(b[1])
        return np.array(lo), np.array(hi)

    def _residuals(self, p, tw, vw):
        rate, d_start, rv, sigma, sigma_slope, dd, k = self._unpack(p)
        N = complex(self.N_film.real, k)
        d = d_start + rate * (tw - tw[0])
        sigma_t = sigma + sigma_slope * (tw - tw[0])
        model = self.A * model_reflectance(d, N, rv, sigma_t, dd, self.wavelength)
        res = model - vw
        # soft anchor: keep the window-start thickness near its propagated value.
        # A 5 nm deviation costs as much as 1-sigma noise on every point.
        # (Disabled during wide-anchor re-init trials.)
        noise = _noise_estimate(vw)
        weight = 0.0 if self._wide_anchor else 1.0
        anchor = weight * (d_start - self.d_start_window) / 5.0 * noise * np.sqrt(len(vw))
        return np.append(res, anchor)

    # -- window management --------------------------------------------------
    def _fringe_period_s(self):
        n = self.N_film.real
        rate = max(abs(self.rate), 1e-4)
        return self.wavelength / (2.0 * n * rate)

    def _current_window(self):
        t = np.array(self.t)
        # Window must comfortably exceed SPAN_UNLOCK_DAMPING so dd can engage;
        # k-fitting needs multi-fringe envelopes.
        target_fringes = 2.8 if self.allow_k_fit else 1.3
        target_span_s = target_fringes * self._fringe_period_s()
        start = self.window_start_idx
        while (t[-1] - t[start]) > target_span_s and (len(t) - start) > MIN_FIT_POINTS:
            start += 1
        start = max(start, len(t) - MAX_WINDOW_POINTS)
        return start

    def _fit_window(self, tw, vw, span):
        p0 = self._pack(span)
        lo, hi = self._bounds()
        p0 = np.clip(p0, lo, hi)
        # soft_l1 keeps model-mismatch regions (3D recovery transients) from
        # dominating the cost and dragging the fringe fit off
        f_scale = max(3.0 * _noise_estimate(vw), 1e-6)
        return least_squares(self._residuals, p0, bounds=(lo, hi), args=(tw, vw),
                             method="trf", max_nfev=200, loss="soft_l1", f_scale=f_scale)

    def _reinit_rate(self, tw, vw, span, best_fit):
        """Multi-start over rate candidates when the fit looks stuck (rate
        collapsed to ~0 on a deposition, or residuals far above noise).
        Trials start from clean state (modest sigma, initial r_v) with the
        thickness anchor widened to half a fringe, so a poisoned warm start
        can't drag every candidate into the same local minimum."""
        saved = (self.rate, self.sigma, self.sigma_slope, self.r_v, self._wide_anchor)
        best_cost = best_fit.cost
        best_x = None
        for cand in RATE_REINIT_GRID_NM_S:
            self.rate = cand if self.segment.kind == "deposition" else -cand
            self.sigma = 5.0
            self.sigma_slope = 0.0
            self.r_v = self.r_v_init
            self._wide_anchor = True
            try:
                trial = self._fit_window(tw, vw, span)
            except Exception:
                continue
            if trial.cost < best_cost:
                best_cost, best_x = trial.cost, trial
        self.rate, self.sigma, self.sigma_slope, self.r_v, self._wide_anchor = saved
        return best_x

    # -- main entry ----------------------------------------------------------
    def add_point(self, t, v):
        self.t.append(t)
        self.v.append(v)
        if len(self.t) < MIN_FIT_POINTS:
            return None

        if self.roughening_onset_t is not None and self.segment.kind == "etch":
            return self._post_onset_point(t)

        new_start = self._current_window()
        if new_start != self.window_start_idx:
            # advance the thickness and roughness anchors to the new window start
            dt_shift = self.t[new_start] - self.t[self.window_start_idx]
            self.d_start_window += self.rate * dt_shift
            self.sigma = float(np.clip(self.sigma + self.sigma_slope * dt_shift, 0.0, 60.0))
            self.window_start_idx = new_start

        tw = np.array(self.t[self.window_start_idx:])
        vw = np.array(self.v[self.window_start_idx:])

        if not self._sigma_initialized:
            # rough-start segment: solve sigma from the opening signal level
            R_smooth = float(model_reflectance(self.d0, self.N_film, self.r_v,
                                               0.0, 0.0, self.wavelength)[0])
            dw = float(np.mean(vw[:4])) / max(self.A * R_smooth, 1e-9)
            if dw < 1.0:
                self.sigma = self.wavelength / (4.0 * np.pi) * np.sqrt(np.log(1.0 / max(dw, 1e-3)))
            self._sigma_initialized = True

        span_rate = max(abs(self.rate), SPAN_RATE_FLOOR_NM_S)
        span = span_rate * (tw[-1] - tw[0]) * 2.0 * self.N_film.real / self.wavelength

        try:
            fit = self._fit_window(tw, vw, span)
        except Exception as err:
            logger.warning(f"Fit failed at t={t:.1f}: {err}")
            return None

        noise = _noise_estimate(vw)
        resid = float(np.sqrt(np.mean(fit.fun[:-1] ** 2)))
        stuck = (self.segment.kind == "deposition" and abs(self.rate) < 0.02
                 and (tw[-1] - tw[0]) > 120.0)
        if (stuck or resid > 5.0 * noise) and len(self.results) >= self._reinit_block_until:
            better = self._reinit_rate(tw, vw, span, fit)
            if better is not None:
                fit = better
            else:
                # nothing better out there - don't burn CPU retrying every point
                self._reinit_block_until = len(self.results) + REINIT_COOLDOWN_POINTS

        rate, d_start, rv, sigma, sigma_slope, dd, k = self._unpack(fit.x)
        self.rate, self.d_start_window, self.r_v = rate, d_start, rv
        self.sigma, self.sigma_slope, self.dd, self.k = sigma, sigma_slope, dd, k

        # uncertainty on rate from the Jacobian
        rate_unc = np.inf
        try:
            J = fit.jac
            dof = max(len(fit.fun) - len(fit.x), 1)
            s2 = float(np.sum(fit.fun ** 2)) / dof
            cov = np.linalg.inv(J.T @ J) * s2
            rate_unc = float(np.sqrt(max(cov[0, 0], 0.0)))
        except np.linalg.LinAlgError:
            pass

        d_now = d_start + rate * (tw[-1] - tw[0])
        sigma_now = float(np.clip(sigma + sigma_slope * (tw[-1] - tw[0]), 0.0, 60.0))
        resid_rms = float(np.sqrt(np.mean((fit.fun[:-1]) ** 2)))

        flags = []
        if self._envelope_declining(t):
            self._roughening_streak += 1
        else:
            self._roughening_streak = 0
        if self._roughening_streak >= ROUGHENING_PERSIST_POINTS:
            flags.append("ROUGHENING")
        elif self.segment.kind == "deposition" and self._peak_decay_warning():
            flags.append("ENVELOPE_DECAY")
        # identifiability: the fitted model must show real fringe contrast in
        # this window, otherwise the rate is riding on nothing
        d_win = d_start + rate * (tw - tw[0])
        model_win = self.A * model_reflectance(d_win, complex(self.N_film.real, k),
                                               rv, sigma_now, dd, self.wavelength)
        contrast = float(np.ptp(model_win))
        if rate_unc > max(0.25 * abs(rate), 0.02) or contrast < MIN_CONTRAST_OVER_NOISE * noise:
            flags.append("RATE_UNCERTAIN")
        recent = np.mean(vw[-3:])
        if recent < SUBSTRATE_CROSS_FRACTION * self.substrate_level_v and self.segment.kind == "etch":
            flags.append("BELOW_SUBSTRATE")
            if self.roughening_onset_t is None:
                self.roughening_onset_t = t
                logger.info(f"Roughening onset detected at t={t:.1f}s (signal below substrate level)")

        # During sustained unidentifiability (fringes washed out) the branch is
        # unconstrained and re-inits can walk the thickness anywhere: freeze at
        # the last confident value instead of reporting a random branch.
        if "RATE_UNCERTAIN" in flags:
            self._uncertain_streak += 1
            if self._uncertain_streak > 5 and self.results:
                d_now = self.results[-1].d_nm
                flags.append("D_FROZEN")
        else:
            self._uncertain_streak = 0

        result = FitResult(t=t, d_nm=d_now, rate_nm_s=rate, rate_unc_nm_s=rate_unc,
                           sigma_nm=sigma_now, dd_nm=dd, k=k, r_v=rv,
                           resid_rms=resid_rms, span_fringes=span, flags=flags)
        self.results.append(result)
        return result

    def _envelope_declining(self, t_now):
        """EWMA-envelope roughening detector - see the constants block.
        The fringe clock is frozen at the amplitude maximum so a collapsed
        rate estimate after washout cannot stall the filter, and 'falling'
        is judged against half a fringe of history, not point-to-point."""
        if len(self.t) < 2:
            return False
        v = self.v[-1]
        dt = self.t[-1] - self.t[-2]
        fringe_ref = self._env_fringe_ref or self._fringe_period_s()
        alpha = float(np.clip(dt / fringe_ref, 0.0, 0.5))

        if self._env_mean is None:
            self._env_mean = v
            self._env_amp = 0.0
            return False
        prev_mean = self._env_mean
        self._env_mean += alpha * (v - self._env_mean)
        self._env_amp += alpha * (abs(v - prev_mean) - self._env_amp)
        self._env_hist.append((t_now, self._env_mean))
        self._env_hist = [(t0, m) for (t0, m) in self._env_hist
                          if t0 >= t_now - 3.0 * fringe_ref]
        # require a bit of history before the running max is meaningful
        if len(self.v) < MIN_FIT_POINTS * 2:
            return False
        if self._env_amp > self._env_amp_max:
            self._env_amp_max = self._env_amp
            self._env_mean_at_amp_max = self._env_mean
            self._env_fringe_ref = self._fringe_period_s()

        if self._env_amp_max <= 0 or self._env_mean_at_amp_max is None:
            return False
        past = [m for (t0, m) in self._env_hist if t0 <= t_now - 0.5 * fringe_ref]
        falling = bool(past) and self._env_mean < past[-1]
        return (self._env_amp < ROUGH_AMP_FRACTION * self._env_amp_max
                and self._env_mean < ROUGH_MEAN_FRACTION * self._env_mean_at_amp_max
                and falling)

    def _peak_decay_warning(self):
        """Early soft warning: three consecutive fringe peaks each
        PEAK_DECAY_RATIO or more below the previous one."""
        v = np.array(self.v)
        tt = np.array(self.t)
        if len(v) < 12:
            return False
        vs = np.convolve(v, np.ones(3) / 3.0, mode="same")
        dt = float(np.median(np.diff(tt)))
        dist = max(3, int(0.5 * self._fringe_period_s() / max(dt, 1e-6)))
        prom = max(5.0 * _noise_estimate(v[-50:]), 0.05 * float(np.ptp(vs)))
        peaks, _ = find_peaks(vs, distance=dist, prominence=prom)
        if len(peaks) < 3:
            return False
        p1, p2, p3 = vs[peaks[-3]], vs[peaks[-2]], vs[peaks[-1]]
        return p3 < PEAK_DECAY_RATIO * p2 and p2 < PEAK_DECAY_RATIO * p1

    def _post_onset_point(self, t):
        """After roughening onset in an etch segment the planar model is
        invalid: freeze thickness, solve sigma algebraically from the signal
        level, and report no rate."""
        d_frozen = self.results[-1].d_nm if self.results else self.d0
        v_recent = float(np.mean(self.v[-3:]))
        R_smooth = float(model_reflectance(d_frozen, self.N_film, self.r_v,
                                           0.0, self.dd, self.wavelength)[0])
        dw = v_recent / max(self.A * R_smooth, 1e-9)
        sigma = 0.0
        if dw < 1.0:
            sigma = self.wavelength / (4.0 * np.pi) * np.sqrt(np.log(1.0 / max(dw, 1e-3)))
        self.sigma = sigma
        result = FitResult(t=t, d_nm=d_frozen, rate_nm_s=float("nan"),
                           rate_unc_nm_s=float("nan"), sigma_nm=sigma, dd_nm=self.dd,
                           k=self.k, r_v=self.r_v, resid_rms=0.0, span_fringes=0.0,
                           flags=["POST_ONSET", "ROUGHENING"])
        self.results.append(result)
        return result

    def model_curve(self):
        """(t, model_V) over all points fed so far, using each point's fit history."""
        if not self.results:
            return np.array([]), np.array([])
        t = np.array([r.t for r in self.results])
        v = np.array([self.A * model_reflectance(r.d_nm, complex(self.N_film.real, r.k),
                                                 r.r_v, r.sigma_nm, r.dd_nm,
                                                 self.wavelength)[0]
                      for r in self.results])
        return t, v


# ---------------------------------------------------------------------------
# Pyrometry

@dataclass
class PyroCalibration:
    temp_c: float
    signal: float       # radiometric channel value at calibration
    reflectance: float  # absolute reflectance at calibration (V / A / 1.0 scale)


def wien_temperature(signal, reflectance_abs, cal,
                     wavelength_nm=materials.LASER_WAVELENGTH_NM):
    """Emissivity-corrected temperature (C) via the Wien-form relation.
    Valid for opaque substrates (Si); on sapphire this is an effective,
    pocket-weighted temperature."""
    if not cal.signal > 0:
        return float("nan")
    T_cal = cal.temp_c + 273.15
    eps_ratio = (1.0 - cal.reflectance) / max(1.0 - reflectance_abs, 1e-6)
    arg = (signal / cal.signal) * eps_ratio
    if arg <= 0:
        return float("nan")
    inv_T = 1.0 / T_cal - (wavelength_nm * 1e-9 / C2_M_K) * np.log(arg)
    if inv_T <= 0:
        return float("nan")
    return 1.0 / inv_T - 273.15


def etch_rate_temperature(rate_nm_s):
    """Temperature (C) implied by a GaN thermal etch rate, via Arrhenius with
    Ea from Koleske et al. APL 82, 1170. Prefactor anchor is approximate until
    calibrated with a dedicated etch run."""
    rate = abs(rate_nm_s)
    if rate <= 0:
        return float("nan")
    T_anchor = materials.GAN_ETCH_RATE_ANCHOR_TEMP_C + 273.15
    ea_over_k = materials.GAN_ETCH_EA_EV / K_B_EV
    r0 = materials.GAN_ETCH_RATE_ANCHOR_NM_S * np.exp(ea_over_k / T_anchor)
    T = ea_over_k / np.log(r0 / rate)
    return T - 273.15


# ---------------------------------------------------------------------------
# Offline orchestration

class OfflineAnalysis:
    """Replays a recorded run through the live fitting path, segment by segment."""

    def __init__(self, run, substrate_name, segments, allow_k_fit=False):
        self.run = run
        self.substrate_name = substrate_name
        self.segments = sorted(segments, key=lambda s: s.t_start)
        self.allow_k_fit = allow_k_fit
        self.A = None
        self.analyzers = []   # (segment, SegmentAnalyzer|None)

    def calibrate_from_substrate_segment(self):
        sub_segs = [s for s in self.segments if s.kind == "substrate"]
        if not sub_segs:
            raise ValueError("No substrate segment defined - cannot calibrate scale")
        s = sub_segs[0]
        mask = (self.run.t >= s.t_start) & (self.run.t <= (s.t_end or self.run.t[-1]))
        level = float(np.median(self.run.refl[mask]))
        R_theory = bare_substrate_reflectance(self.substrate_name, s.temp_c)
        self.A = level / R_theory
        logger.info(f"Calibration: V={level:.4f} on {self.substrate_name} "
                    f"(R={R_theory:.4f}) -> A={self.A:.4f} V/unit")
        return self.A

    def run_all(self):
        if self.A is None:
            self.calibrate_from_substrate_segment()

        prev_analyzer_by_material = {}
        roughened = False   # any prior segment ended rough -> next dep starts rough
        for seg in self.segments:
            if seg.kind == "substrate":
                self.analyzers.append((seg, None))
                continue

            d0, rv_init = 0.0, None
            prev = prev_analyzer_by_material.get(seg.material)
            if seg.kind == "etch":
                if prev is not None and prev.results:
                    d0 = prev.results[-1].d_nm
                    rv_init = prev.r_v
                else:
                    logger.warning(f"Etch segment at t={seg.t_start:.0f} has no prior "
                                   f"deposition of {seg.material}; starting d0=0")

            ana = SegmentAnalyzer(seg, self.A, self.substrate_name,
                                  d0_nm=d0, r_v_init=rv_init,
                                  allow_k_fit=self.allow_k_fit,
                                  start_rough=(seg.kind == "deposition" and roughened))
            mask = (self.run.t >= seg.t_start) & (self.run.t <= (seg.t_end or self.run.t[-1]))
            for t, v in zip(self.run.t[mask], self.run.refl[mask]):
                ana.add_point(t, v)
            self.analyzers.append((seg, ana))
            prev_analyzer_by_material[seg.material] = ana
            if ana.roughening_onset_t is not None or \
                    (ana.results and "ROUGHENING" in ana.results[-1].flags):
                roughened = True
            elif seg.kind == "deposition" and ana.results:
                roughened = False   # a smooth deposition resets the surface

            last = ana.results[-1] if ana.results else None
            if last:
                logger.info(
                    f"Segment {seg.kind}/{seg.material} [{seg.t_start:.0f}-{seg.t_end or 'end'}]: "
                    f"final d={last.d_nm:.1f} nm, rate={last.rate_um_hr:.2f} um/hr "
                    f"(±{last.rate_unc_um_hr:.2f}), sigma={last.sigma_nm:.1f} nm, "
                    f"onset={ana.roughening_onset_t}")
        return self.analyzers

    def save_results_csv(self, path):
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Time (s)", "Segment", "Material", "Thickness (nm)",
                        "Rate (um/hr)", "Rate unc (um/hr)", "Roughness sigma (nm)",
                        "Nonuniformity (nm)", "k", "Residual RMS (V)", "Flags"])
            for seg, ana in self.analyzers:
                if ana is None:
                    continue
                for r in ana.results:
                    w.writerow([f"{r.t:.2f}", seg.kind, seg.material,
                                f"{r.d_nm:.2f}", f"{r.rate_um_hr:.3f}",
                                f"{r.rate_unc_um_hr:.3f}", f"{r.sigma_nm:.2f}",
                                f"{r.dd_nm:.2f}", f"{r.k:.5f}",
                                f"{r.resid_rms:.5f}", "|".join(r.flags)])


# ---------------------------------------------------------------------------
# CLI

def plot_analysis(run, analysis, out_prefix):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(4, 1, figsize=(12, 12), sharex=True)
    axes[0].plot(run.t, run.refl, lw=0.8, label="measured")
    for seg, ana in analysis.analyzers:
        if ana is None or not ana.results:
            continue
        tm, vm = ana.model_curve()
        axes[0].plot(tm, vm, lw=1.2, label=f"fit {seg.material}")
        axes[1].plot([r.t for r in ana.results], [r.d_nm for r in ana.results])
        rates = np.array([r.rate_um_hr for r in ana.results])
        uncs = np.array([r.rate_unc_um_hr for r in ana.results])
        tt = np.array([r.t for r in ana.results])
        axes[2].plot(tt, rates)
        axes[2].fill_between(tt, rates - uncs, rates + uncs, alpha=0.25)
        axes[3].plot(tt, [r.sigma_nm for r in ana.results], label=f"sigma {seg.material}")
        axes[3].plot(tt, [r.dd_nm for r in ana.results], ls="--", label=f"dd {seg.material}")
    axes[0].set_ylabel("Signal (V)")
    axes[0].legend(fontsize=8)
    axes[1].set_ylabel("Thickness (nm)")
    axes[2].set_ylabel("Rate (um/hr)")
    axes[3].set_ylabel("Roughness / nonuniformity (nm)")
    axes[3].set_xlabel("Time (s)")
    axes[3].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(out_prefix + "_analysis.png", dpi=110)
    plt.close(fig)
    return out_prefix + "_analysis.png"


def main():
    ap = argparse.ArgumentParser(description="Offline RTLR reflectance analysis")
    ap.add_argument("run", help="Run directory or Reflectance.csv path")
    ap.add_argument("--segments", required=True, help="segments.json path")
    ap.add_argument("--out", default=None, help="Output prefix (default: alongside run)")
    ap.add_argument("--fit-k", action="store_true", help="Allow k as a fit parameter")
    ap.add_argument("--plot", action="store_true", help="Save analysis plots")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    run = load_run(args.run)
    substrate, segments = load_segments(args.segments)
    analysis = OfflineAnalysis(run, substrate, segments, allow_k_fit=args.fit_k)
    analysis.run_all()

    out = args.out or os.path.join(os.path.dirname(run.path), "Analysis")
    analysis.save_results_csv(out + ".csv")
    print(f"Results written to {out}.csv")
    if args.plot:
        png = plot_analysis(run, analysis, out)
        print(f"Plots written to {png}")


if __name__ == "__main__":
    main()
