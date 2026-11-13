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


class StreamingSerial:
    """Fake SR830 whose buffer grows by `step` points per SPTS? query and
    answers TRCB ? ch, start, k with the matching slice of known data."""

    def __init__(self, r, ch2, step):
        self.data = {1: np.asarray(r, dtype="<f4"), 2: np.asarray(ch2, dtype="<f4")}
        self.step, self.stored, self.pending, self.sent = step, 0, b"", []

    def write(self, data):
        cmd = data.decode().strip()
        self.sent.append(cmd)
        if cmd == "SPTS ?":
            self.stored = min(self.stored + self.step, len(self.data[1]))
            self.pending += f"{self.stored}\r".encode()
        elif cmd.startswith("TRCB ?"):
            ch, start, k = (int(x) for x in cmd[6:].split(","))
            self.pending += self.data[ch][start:start + k].tobytes()
        elif cmd == "REST":
            self.stored = 0

    def read(self, n):
        out, self.pending = self.pending[:n], self.pending[n:]
        return out


@pytest.fixture
def stream_config(monkeypatch, tmp_path):
    monkeypatch.setattr(common, "SRS830_FAKE_SERIAL", False)
    monkeypatch.setattr(common, "SRS830_CAPTURE_CH2", True)
    monkeypatch.setattr(common, "SRS830_CH2_SIGN", -1)
    monkeypatch.setattr(common, "SRS830_STREAM_RATE_HZ", 128)
    monkeypatch.setattr(common, "SRS830_STREAM_BLOCK_S", 1.0)
    monkeypatch.setattr(common, "SRS830_STREAM_SAVE_RAW", True)
    return tmp_path


def make_stream_handler(con, run_dir):
    import queue, threading
    h = make_handler(con)
    h.run_dir = str(run_dir)
    h.queue_data_out = queue.Queue()
    h.event_end = threading.Event()
    h.logger = srs830.logger
    h.stream_start()
    return h


def test_stream_emits_one_second_blocks_in_order(stream_config):
    n = 128 * 3 + 40
    r = np.arange(n) * 1e-4
    con = StreamingSerial(r, -2 * r, step=50)  # reads straddle block boundaries
    h = make_stream_handler(con, stream_config)
    while h.stream_nread < n:
        h.stream_read_new()
        h.stream_emit()
    blocks = [h.queue_data_out.get_nowait() for _ in range(h.queue_data_out.qsize())]
    assert len(blocks) == 3                                   # 40 leftover points stay pending
    got = np.concatenate([b[1][1] for b in blocks])
    np.testing.assert_allclose(got, r[:384], rtol=1e-6)      # contiguous, nothing lost or repeated
    np.testing.assert_allclose(np.concatenate([b[1][2] for b in blocks]), 2 * r[:384], rtol=1e-6)  # CH2 sign flipped
    starts = [b[0] - h.stream_t0 for b in blocks]
    np.testing.assert_allclose(starts, [0.0, 1.0, 2.0], atol=1e-9)
    assert all(len(b[1][0]) == 128 for b in blocks)


def test_stream_flush_emits_partial_block_only_if_half_full(stream_config):
    con = StreamingSerial(np.ones(128 + 70), np.ones(128 + 70), step=200)
    h = make_stream_handler(con, stream_config)
    h.stream_read_new()
    h.stream_emit(flush=True)
    sizes = [len(h.queue_data_out.get_nowait()[1][1]) for _ in range(h.queue_data_out.qsize())]
    assert sizes == [128, 70]


def test_stream_raw_file_has_every_sample(stream_config):
    n = 300
    r = np.linspace(0.01, 0.02, n)
    con = StreamingSerial(r, r, step=77)
    h = make_stream_handler(con, stream_config)
    while h.stream_nread < n:
        h.stream_read_new()
    raw = np.loadtxt(stream_config / "raw_stream.csv", delimiter=",", skiprows=1)
    assert raw.shape == (n, 3)
    np.testing.assert_allclose(np.diff(raw[:, 0]), 1 / 128, atol=1e-3)
    np.testing.assert_allclose(raw[:, 1], r, rtol=1e-6)


def test_stream_restart_refreshes_reference_phase_last(stream_config):
    con = StreamingSerial(np.ones(10), np.ones(10), step=10)
    make_stream_handler(con, stream_config)
    i_freq = max(i for i, c in enumerate(con.sent) if c.startswith("FREQ"))
    i_phas = max(i for i, c in enumerate(con.sent) if c.startswith("PHAS"))
    assert i_phas > i_freq


def test_stream_restart_relocks_front_panel(stream_config):
    con = StreamingSerial(np.ones(10), np.ones(10), step=10)
    make_stream_handler(con, stream_config)
    assert "LOCL 1" in con.sent


def test_configure_does_not_reset_and_clears_offsets(monkeypatch):
    # *RST would put the reference display (and the failed knob) back on FREQ
    con = ScriptedSerial({"*IDN ?": "Stanford_Research_Systems,SR830,s/n0,ver0"})
    con.reset_input_buffer = lambda: None
    monkeypatch.setattr(srs830.serial, "Serial", lambda *a, **k: con)
    monkeypatch.setattr(common, "SRS830_SETTLE_TIME_S", 0)
    h = make_handler(None)
    h.serialPort = "fake"
    h.logger = srs830.logger
    import threading
    h.event_end = threading.Event()
    h.connect_and_configure()
    assert "*RST" not in con.sent
    for ch in (1, 2, 3):
        assert f"OEXP {ch},0,0" in con.sent
    assert {"HARM 1", "ILIN 0", "SYNC 0", "OFSL 1", "PHAS 0", "LOCL 1"} <= set(con.sent)
