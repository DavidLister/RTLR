# Tests for the live pyrometer (CH2 minus laser leak, ~1.2 um Wien).
# Run with: pytest tests/ -v

import os
import sys
import math

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common
import pyrometer

S432_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "lab_notes", "2026-11-13_RTLR", "2026-11-13_15-19-06---S432")


def leak_ch2(p, r, thermal=0.0):
    return p.a + p.g * r + thermal


def test_reference_signal_reads_reference_temperature():
    T = pyrometer.wien_temperature_c(common.PYRO_REF_SIGNAL_V, common.PYRO_REF_SIGNAL_V,
                                     common.PYRO_REF_TEMP_C)
    assert T == pytest.approx(common.PYRO_REF_TEMP_C, abs=1e-9)


def test_wien_inverts_at_effective_wavelength():
    lam = common.PYRO_LAMBDA_EFF_UM * 1e-6
    T_ref, T_hot = 1000.0 + 273.15, 1020.0 + 273.15
    s = math.exp((pyrometer.C2_M_K / lam) * (1 / T_ref - 1 / T_hot))
    assert pyrometer.wien_temperature_c(s, 1.0, 1000.0) == pytest.approx(1020.0, abs=1e-6)


def test_nonpositive_signal_is_nan():
    assert math.isnan(pyrometer.wien_temperature_c(0.0, 1.0, 1000.0))
    assert math.isnan(pyrometer.wien_temperature_c(-0.1, 1.0, 1000.0))
    assert math.isnan(pyrometer.wien_temperature_c(0.5, 0.0, 1000.0))


def test_leak_is_subtracted():
    p = pyrometer.LivePyrometer(auto_leak_s=0)
    r = 0.0076
    T = p.update(10.0, r, leak_ch2(p, r, common.PYRO_REF_SIGNAL_V))
    assert T == pytest.approx(common.PYRO_REF_TEMP_C, abs=1e-6)


def test_cold_reads_below_range():
    p = pyrometer.LivePyrometer(auto_leak_s=0)
    assert math.isnan(p.update(1.0, 0.017, leak_ch2(p, 0.017)))
    assert math.isnan(p.update(2.0, 0.017, leak_ch2(p, 0.017, 0.010)))  # below the floor


def test_dark_level_tracks_cold_laser_off_blocks():
    p = pyrometer.LivePyrometer(auto_leak_s=0)
    for i in range(20):
        p.update(float(i), 0.0, -0.0007)
    assert p.a == pytest.approx(-0.0007)
    assert "laser off" in p.a_source


def test_hot_beam_block_is_a_leak_free_reading_not_a_dark_level():
    p = pyrometer.LivePyrometer(auto_leak_s=0)
    a0 = p.a
    T = p.update(1.0, 0.0, a0 + common.PYRO_REF_SIGNAL_V)
    assert p.a == a0  # thermal signal must not be taken as the dark offset
    assert T == pytest.approx(common.PYRO_REF_TEMP_C, abs=1e-6)


def test_auto_leak_from_cold_start():
    p = pyrometer.LivePyrometer(g=6.4, auto_leak_s=30)
    rng = np.random.default_rng(0)
    for i in range(40):
        r = 0.017 * (1 + 0.01 * rng.standard_normal())
        p.update(float(i), r, p.a + 6.9 * r)   # true leak 6.9, default 6.4
    assert p.g == pytest.approx(6.9, rel=0.01)
    assert "cold start" in p.g_source
    assert not p.auto_leak_active


def test_auto_leak_abandoned_when_starting_hot():
    p = pyrometer.LivePyrometer(g=6.4, auto_leak_s=30)
    for i in range(40):
        p.update(float(i), 0.008, p.a + 6.4 * 0.008 + 1.0)   # already at ~1000 C
    assert p.g == 6.4
    assert p.g_source.startswith("default")


def test_manual_leak_calibration_and_reference():
    p = pyrometer.LivePyrometer(g=6.4, auto_leak_s=0)
    for i in range(30):
        p.update(float(i), 0.017, p.a + 7.2 * 0.017)
    assert "7.20" in p.calibrate_leak()
    assert p.g == pytest.approx(7.2)
    # refuse a T reference while cold
    assert "too cold" in p.set_reference(1020.0)
    for i in range(30, 45):
        p.update(float(i), 0.017, p.a + 7.2 * 0.017 + 0.8)
    assert "set" in p.set_reference(1020.0)
    assert p.update(46.0, 0.017, p.a + 7.2 * 0.017 + 0.8) == pytest.approx(1020.0, abs=1e-6)
    p.reset_reference()
    assert p.ref_label == "S432 scale"


def test_window_stats():
    p = pyrometer.LivePyrometer(auto_leak_s=0)
    for i in range(100):
        p.update(float(i), 0.0076, leak_ch2(p, 0.0076, common.PYRO_REF_SIGNAL_V))
    mean, sd, n = p.window_stats(60)
    assert n == 60
    assert mean == pytest.approx(common.PYRO_REF_TEMP_C, abs=1e-6)
    assert sd == pytest.approx(0.0, abs=1e-9)


def test_csv_row_matches_header():
    p = pyrometer.LivePyrometer(auto_leak_s=0)
    p.update(1.0, 0.0076, leak_ch2(p, 0.0076, 1.0))
    assert len(pyrometer.csv_row(p, 1.0).split(",")) == len(pyrometer.CSV_HEADER.split(","))


@pytest.mark.skipif(not os.path.exists(os.path.join(S432_DIR, "raw_stream.csv")),
                    reason="S432 lab data not present (lab_notes is local only)")
def test_s432_stream_reproduces_thermocouple_plateaus():
    import json
    import pandas as pd
    origin = json.load(open(os.path.join(S432_DIR, "raw", "instrument.json")))["time_origin_epoch"]
    s = pd.read_csv(os.path.join(S432_DIR, "raw_stream.csv"))
    s.columns = ["t", "R", "C"]
    s["t"] -= origin
    b = s.groupby(np.floor(s.t).astype(int)).agg(R=("R", "mean"), C=("C", "mean"))
    p = pyrometer.LivePyrometer()
    T = np.array([p.update(float(t) + 0.5, r, c) for t, r, c in zip(b.index, b.R, b.C)])
    t = b.index.values + 0.5
    assert "cold start" in p.g_source and "laser off" in p.a_source
    bake = np.nanmean(T[(t > 2000) & (t < 2250)])       # TC 1020.2 C
    dep = np.nanmean(T[(t > 3800) & (t < 5500)])        # TC 1000.8 C
    assert bake == pytest.approx(1020.2, abs=4.0)
    assert dep == pytest.approx(1000.8, abs=2.0)
    assert np.isnan(T[(t > 300) & (t < 400)]).all()     # ~100 C: below range
