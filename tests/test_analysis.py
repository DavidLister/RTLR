# Synthetic ground-truth tests for the RTLR analysis engine.
# Run with: pytest tests/ -v

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analysis
import materials

WL = materials.LASER_WAVELENGTH_NM
RNG = np.random.default_rng(7)


def make_signal(t, G_nm_s, N, r_v, A, sigma_nm=0.0, dd_nm=0.0, d0=0.0, noise=0.0008):
    d = d0 + G_nm_s * t
    R = analysis.model_reflectance(d, N, r_v, sigma_nm, dd_nm, WL)
    return A * R + RNG.normal(0, noise, len(t))


def ht_gan_setup(temp_c=1050):
    N = materials.FILMS["HT GaN"].nk(temp_c)
    N_sub = materials.SUBSTRATES["Al2O3"].nk(temp_c)
    return N, analysis.fresnel_r(N, N_sub)


def feed(ana, t, v):
    last = None
    for ti, vi in zip(t, v):
        r = ana.add_point(ti, vi)
        if r is not None:
            last = r
    return last


def test_deposition_rate_recovery():
    N, rv = ht_gan_setup()
    G, A = 0.44, 0.30
    t = np.arange(0, 2500, 8.2)
    v = make_signal(t, G, N, rv, A, sigma_nm=2.0, dd_nm=6.0)
    seg = analysis.Segment(kind="deposition", t_start=0, t_end=2500,
                           material="HT GaN", temp_c=1050)
    ana = analysis.SegmentAnalyzer(seg, A, "Al2O3")
    last = feed(ana, t, v)
    assert last is not None
    assert last.rate_um_hr == pytest.approx(G * 3.6, rel=0.02)
    assert last.d_nm == pytest.approx(G * t[-1], abs=8.0)


def test_rate_tracks_change():
    # rate step: 0.44 -> 0.30 nm/s halfway through
    N, rv = ht_gan_setup()
    A = 0.30
    t = np.arange(0, 4000, 8.2)
    G = np.where(t < 2000, 0.44, 0.30)
    d = np.cumsum(np.append(0, np.diff(t)) * G)
    R = analysis.model_reflectance(d, N, rv, 0.0, 0.0, WL)
    v = A * R + RNG.normal(0, 0.0008, len(t))
    seg = analysis.Segment(kind="deposition", t_start=0, t_end=4000,
                           material="HT GaN", temp_c=1050)
    ana = analysis.SegmentAnalyzer(seg, A, "Al2O3")
    feed(ana, t, v)
    late = [r for r in ana.results if r.t > 3400]
    assert np.median([r.rate_um_hr for r in late]) == pytest.approx(0.30 * 3.6, rel=0.05)
    # total thickness integrates through the step
    assert ana.results[-1].d_nm == pytest.approx(d[-1], abs=15.0)


def test_rough_start_recovery():
    # 3D-recovery start: sigma decays 35 -> 5 nm over the first 800 s
    N, rv = ht_gan_setup()
    G, A = 0.44, 0.30
    t = np.arange(0, 2500, 8.2)
    sigma_t = np.clip(35.0 - 0.0375 * t, 5.0, None)
    d = G * t
    R = analysis.model_reflectance(d, N, rv, sigma_t, 0.0, WL)
    v = A * R + RNG.normal(0, 0.0008, len(t))
    seg = analysis.Segment(kind="deposition", t_start=0, t_end=2500,
                           material="HT GaN", temp_c=1050)
    ana = analysis.SegmentAnalyzer(seg, A, "Al2O3", start_rough=True)
    last = feed(ana, t, v)
    assert last.rate_um_hr == pytest.approx(G * 3.6, rel=0.06)
    assert last.d_nm == pytest.approx(G * t[-1], rel=0.05)
    # sigma should have relaxed to near its true final value
    assert last.sigma_nm < 12.0


def test_etch_rate_recovery():
    # planar etch of LT GaN from 40 nm at -0.03 nm/s (sub-fringe, smooth)
    temp = 1000
    N = materials.FILMS["LT GaN"].nk(temp)
    N_sub = materials.SUBSTRATES["Al2O3"].nk(temp)
    rv = analysis.fresnel_r(N, N_sub)
    G, A, d0 = -0.03, 0.78, 40.0
    t = np.arange(0, 600, 4.5)
    v = make_signal(t, G, N, rv, A, d0=d0, noise=0.0004)
    seg = analysis.Segment(kind="etch", t_start=0, t_end=600,
                           material="LT GaN", temp_c=temp)
    ana = analysis.SegmentAnalyzer(seg, A, "Al2O3", d0_nm=d0, r_v_init=rv)
    last = feed(ana, t, v)
    assert last.rate_um_hr == pytest.approx(G * 3.6, rel=0.25)
    assert ana.roughening_onset_t is None


def test_roughening_onset_detection():
    # planar etch that transitions to island roughening: signal driven below
    # the bare-substrate level by a growing DW factor
    temp = 1000
    N = materials.FILMS["LT GaN"].nk(temp)
    N_sub = materials.SUBSTRATES["Al2O3"].nk(temp)
    rv = analysis.fresnel_r(N, N_sub)
    A, d0 = 0.78, 35.0
    t = np.arange(0, 700, 4.5)
    d = np.clip(d0 - 0.05 * t, 5.0, None)
    sigma_t = np.where(t < 300, 0.0, (t - 300) * 0.12)
    R = analysis.model_reflectance(d, N, rv, sigma_t, 0.0, WL)
    v = A * R + RNG.normal(0, 0.0004, len(t))
    seg = analysis.Segment(kind="etch", t_start=0, t_end=700,
                           material="LT GaN", temp_c=temp)
    ana = analysis.SegmentAnalyzer(seg, A, "Al2O3", d0_nm=d0, r_v_init=rv)
    feed(ana, t, v)
    assert ana.roughening_onset_t is not None
    assert 300 < ana.roughening_onset_t < 650
    # post-onset results must not report a rate
    post = [r for r in ana.results if "POST_ONSET" in r.flags]
    assert post and all(np.isnan(r.rate_nm_s) for r in post)
    # and sigma should be rising
    assert post[-1].sigma_nm > 10.0


def test_deposition_roughening_alarm():
    # healthy fringes for 2 fringes, then roughness ramps up and collapses
    # the envelope -> ROUGHENING must flag during the collapse, not before
    N, rv = ht_gan_setup()
    G, A = 0.25, 0.27
    t = np.arange(0, 5800, 8.2)
    t_onset = 2200.0
    sigma_t = np.where(t < t_onset, 2.0, 2.0 + 0.025 * (t - t_onset))
    d = G * t
    R = analysis.model_reflectance(d, N, rv, sigma_t, 0.0, WL)
    v = A * R + RNG.normal(0, 0.0006, len(t))
    seg = analysis.Segment(kind="deposition", t_start=0, t_end=5800,
                           material="HT GaN", temp_c=1050)
    ana = analysis.SegmentAnalyzer(seg, A, "Al2O3")
    feed(ana, t, v)
    def detected(r):
        return "ROUGHENING" in r.flags or "ENVELOPE_DECAY" in r.flags
    flagged = [r.t for r in ana.results if detected(r)]
    clean = [r for r in ana.results if r.t < t_onset]
    assert flagged, "roughening was never flagged"
    assert min(flagged) > t_onset, "flagged before roughening started"
    assert sum(detected(r) for r in clean) == 0
    # the confirmed alarm must also fire eventually
    assert any("ROUGHENING" in r.flags for r in ana.results)


def test_no_branch_jump_on_decimated_data():
    N, rv = ht_gan_setup()
    G, A = 0.44, 0.30
    t = np.arange(0, 2500, 16.4)  # half the sample rate
    v = make_signal(t, G, N, rv, A)
    seg = analysis.Segment(kind="deposition", t_start=0, t_end=2500,
                           material="HT GaN", temp_c=1050)
    ana = analysis.SegmentAnalyzer(seg, A, "Al2O3")
    feed(ana, t, v)
    d_series = np.array([r.d_nm for r in ana.results])
    # thickness must be monotone-ish: no jumps bigger than half a fringe
    assert np.max(np.abs(np.diff(d_series))) < 0.5 * WL / (2 * N.real)
    assert ana.results[-1].rate_um_hr == pytest.approx(G * 3.6, rel=0.03)


def test_loader_handles_nans(tmp_path):
    p = tmp_path / "Reflectance.csv"
    p.write_text("Time (s),Reflectance (v),CH2\n"
                 "1.0,0.5,0.1\n2.0,nan,0.1\n3.0,0.6,0.2\n")
    run = analysis.load_run(str(p))
    assert len(run.t) == 2
    assert run.n_dropped == 1
    assert run.ch2 is not None and len(run.ch2) == 2


def test_wien_pyrometry_roundtrip():
    cal = analysis.PyroCalibration(temp_c=900.0, signal=1.0, reflectance=0.10)
    # same conditions -> calibration temperature
    assert analysis.wien_temperature(1.0, 0.10, cal) == pytest.approx(900.0, abs=0.01)
    # hotter: signal up at fixed emissivity
    T_hot = 1000.0 + 273.15
    lam = materials.LASER_WAVELENGTH_NM * 1e-9
    s_hot = np.exp(-(lam / analysis.C2_M_K) ** -1 * 0)  # placeholder, compute below
    # generate signal from the same Wien relation and invert
    inv = 1.0 / (900.0 + 273.15) - 1.0 / T_hot
    s_hot = np.exp(inv * analysis.C2_M_K / lam)
    assert analysis.wien_temperature(s_hot, 0.10, cal) == pytest.approx(1000.0, rel=0.001)


def test_wien_zero_reference_signal_is_nan():
    cal = analysis.PyroCalibration(temp_c=900.0, signal=0.0, reflectance=0.10)
    assert np.isnan(analysis.wien_temperature(0.0, 0.10, cal))


def test_pyro_reference_refused_without_ch2(tmp_path):
    # CH2 capture off -> ch2 is 0.0 on every point. "Set T ref" used to store a
    # zero reference and then raise ZeroDivisionError on every later point,
    # freezing all live readouts for the rest of the run.
    from analysis_panel import AnalysisController
    ctl = AnalysisController(str(tmp_path))
    try:
        for i in range(10):
            ctl._handle(("point", 10.0 * i, 0.05, 0.0))
        ctl._handle(("calibrate", 1050.0))
        assert ctl.A is not None
        ctl._handle(("pyro_ref", 1050.0))
        assert ctl.pyro_cal is None
        assert "CH2" in ctl.snapshot()["status"]
        ctl._handle(("point", 100.0, 0.05, 0.0))  # must not raise
        assert ctl.snapshot()["T"] is None
    finally:
        ctl.stop()


def test_etch_rate_thermometer_anchor():
    T = analysis.etch_rate_temperature(materials.GAN_ETCH_RATE_ANCHOR_NM_S)
    assert T == pytest.approx(materials.GAN_ETCH_RATE_ANCHOR_TEMP_C, abs=0.5)
    # slower etch -> colder
    assert analysis.etch_rate_temperature(1e-4) < T


def test_materials_temperature_correction():
    n_ht = materials.FILMS["HT GaN"].nk(1050).real
    assert 2.45 < n_ht < 2.50
    assert materials.FILMS["HT GaN"].nk(25).real == pytest.approx(2.378)
