# Tests for the SR830 transfer path, using a scripted fake serial line.
# Run with: pytest tests/ -v

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common
import srs830


class ScriptedSerial:
    """Answers each query command with the next scripted response."""

    def __init__(self, responses):
        self.responses = dict(responses)
        self.pending = b""
        self.sent = []

    def write(self, data):
        cmd = data.decode().strip()
        self.sent.append(cmd)
        if cmd in self.responses:
            self.pending += self.responses[cmd].encode() + b"\r"

    def read(self, n):
        out, self.pending = self.pending[:n], self.pending[n:]
        return out


def make_handler(con):
    # Bypass __init__ so no worker thread or real port is involved
    h = srs830.SRS830Handler.__new__(srs830.SRS830Handler)
    h.ser = con
    return h


@pytest.fixture
def ch2_config(monkeypatch):
    monkeypatch.setattr(common, "SRS830_FAKE_SERIAL", False)
    monkeypatch.setattr(common, "SRS830_CAPTURE_CH2", True)
    monkeypatch.setattr(common, "SRS830_CH2_SIGN", -1)


def test_transfer_flips_ch2_sign_only(ch2_config):
    con = ScriptedSerial({
        "SPTS ?": "3",
        "TRCA ? 1, 0, 3": "0.1,0.2,0.3,",
        "TRCA ? 2, 0, 3": "-0.5,-0.6,-0.7,",
    })
    t, r, ch2 = make_handler(con).transfer_data()
    np.testing.assert_allclose(r, [0.1, 0.2, 0.3])       # R untouched
    np.testing.assert_allclose(ch2, [0.5, 0.6, 0.7])     # inverted photodiode made positive
    np.testing.assert_allclose(t, np.arange(3) / common.SRS830_CAPTURE_RATE_HZ)


def test_transfer_without_ch2_returns_zeros(ch2_config, monkeypatch):
    monkeypatch.setattr(common, "SRS830_CAPTURE_CH2", False)
    con = ScriptedSerial({"SPTS ?": "2", "TRCA ? 1, 0, 2": "0.1,0.2,"})
    _, _, ch2 = make_handler(con).transfer_data()
    np.testing.assert_array_equal(ch2, [0.0, 0.0])
    assert not any(c.startswith("TRCA ? 2") for c in con.sent)


def test_configure_selects_aux3_for_ch2(monkeypatch):
    # Default config must point CH2 at the pyrometer photodiode on Aux In 3
    assert common.SRS830_DDEF_CH2 == "2,3,0"
    assert common.SRS830_CAPTURE_CH2 is True
