# Tests for the SR830 transfer path, using a scripted fake serial line.
# Run with: pytest tests/ -v

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common
import srs830


def f32(values):
    # A TRCB reply: little-endian float32, no terminator
    return np.asarray(values, dtype="<f4").tobytes()


class ScriptedSerial:
    """Answers each query command with its scripted response. str responses are
    ASCII and get a CR terminator; bytes responses (binary TRCB) are sent as-is.
    max_chunk caps bytes per read() to mimic a reply arriving over several reads."""

    def __init__(self, responses, max_chunk=None):
        self.responses = dict(responses)
        self.pending = b""
        self.sent = []
        self.max_chunk = max_chunk

    def write(self, data):
        cmd = data.decode().strip()
        self.sent.append(cmd)
        if cmd in self.responses:
            resp = self.responses[cmd]
            self.pending += resp if isinstance(resp, bytes) else resp.encode() + b"\r"

    def read(self, n):
        if self.max_chunk is not None:
            n = min(n, self.max_chunk)
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
        "TRCB ? 1, 0, 3": f32([0.1, 0.2, 0.3]),
        "TRCB ? 2, 0, 3": f32([-0.5, -0.6, -0.7]),
    })
    t, r, ch2 = make_handler(con).transfer_data()
    np.testing.assert_allclose(r, [0.1, 0.2, 0.3], rtol=1e-6)       # R untouched
    np.testing.assert_allclose(ch2, [0.5, 0.6, 0.7], rtol=1e-6)     # inverted photodiode made positive
    np.testing.assert_allclose(t, np.arange(3) / common.SRS830_CAPTURE_RATE_HZ)


def test_transfer_without_ch2_returns_zeros(ch2_config, monkeypatch):
    monkeypatch.setattr(common, "SRS830_CAPTURE_CH2", False)
    con = ScriptedSerial({"SPTS ?": "2", "TRCB ? 1, 0, 2": f32([0.1, 0.2])})
    _, _, ch2 = make_handler(con).transfer_data()
    np.testing.assert_array_equal(ch2, [0.0, 0.0])
    assert not any(c.startswith("TRCB ? 2") for c in con.sent)


def test_binary_reply_split_across_reads_is_reassembled(ch2_config):
    # A long TRCB reply spans many read() calls; each partial read must be kept
    vals = np.linspace(-1e-3, 2e-3, 500)
    con = ScriptedSerial({
        "SPTS ?": "500",
        "TRCB ? 1, 0, 500": f32(vals),
        "TRCB ? 2, 0, 500": f32(-vals),
    }, max_chunk=7)  # not a multiple of 4, so floats straddle reads
    _, r, ch2 = make_handler(con).transfer_data()
    np.testing.assert_allclose(r, vals, rtol=1e-6)
    np.testing.assert_allclose(ch2, vals, rtol=1e-6)


def test_truncated_binary_reply_raises_timeout(ch2_config):
    # The line going silent mid-transfer must raise, not return a short array
    con = ScriptedSerial({"SPTS ?": "3", "TRCB ? 1, 0, 3": f32([0.1, 0.2, 0.3])[:7]})
    with pytest.raises(srs830.SRS830TimeoutError):
        make_handler(con).transfer_data()


def test_configure_selects_aux3_for_ch2(monkeypatch):
    # Default config must point CH2 at the pyrometer photodiode on Aux In 3
    assert common.SRS830_DDEF_CH2 == "2,3,0"
    assert common.SRS830_CAPTURE_CH2 is True
