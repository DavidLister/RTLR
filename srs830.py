# srs830.py
#
# Interface for the SRS830
#
# Paradigm is to take a burst sample, then pause sampling to transfer the data, alternating between burst and transfer.
# Another thread handles any analysis, this library only configures the instrument and handles data transfer.
#
# David Lister
# July 2023

import io
import os
import json
import time
import queue
import logging
import datetime
import serial
import threading
import common
import numpy as np

logger = logging.getLogger("RTLR.srs830")


class SRS830Error(Exception):
    pass


class SRS830TimeoutError(SRS830Error):
    pass


def send_command(con, command):
    if not common.SRS830_FAKE_SERIAL:
        con.write(bytes(command + "\r\n", encoding="utf-8"))


def capture_until_eol(con):
    # The port timeout acts as an inter-byte timeout: read(1) blocks until a byte
    # arrives or the line has been silent for SRS830_TIMEOUT_S. A long transfer
    # never times out as long as bytes keep flowing.
    dout = bytearray()
    while True:
        data = con.read(1)
        if data == b"":
            raise SRS830TimeoutError(f"Serial line silent for {common.SRS830_TIMEOUT_S}s after {len(dout)} bytes")
        if data == b"\r":
            return bytes(dout)
        dout += data


def capture_binary(con, n_bytes):
    # Binary replies have no terminator, so read an exact byte count. pyserial's
    # timeout caps each read() call, not the whole transfer, so loop: a long
    # transfer only times out if the line goes silent for SRS830_TIMEOUT_S.
    dout = bytearray()
    while len(dout) < n_bytes:
        data = con.read(n_bytes - len(dout))
        if data == b"":
            raise SRS830TimeoutError(f"Serial line silent for {common.SRS830_TIMEOUT_S}s after {len(dout)}/{n_bytes} bytes")
        dout += data
    return bytes(dout)


def read_buffer(con, channel, points, start=0):
    # TRCB returns IEEE-754 float32, little-endian, 4 bytes per point - 3.5x
    # fewer bytes than TRCA's ASCII (~14 B/pt), and the RS232 line is the
    # bottleneck. Matches TRCA to its 7 significant digits.
    # (TRCL is no faster and truncates after ~66 bytes over RS232 on fw 0.341.)
    send_command(con, f"TRCB ? {channel}, {start}, {points}")
    return np.frombuffer(capture_binary(con, 4 * points), dtype="<f4").astype(float)


def query(con, command):
    send_command(con, command)
    return capture_until_eol(con).decode("utf-8", errors="replace").strip()


# Settings read back into instrument.json when SRS830_DEBUG_RAW is on
SETTINGS_QUERIES = ["*IDN ?", "FMOD ?", "FREQ ?", "SLVL ?", "PHAS ?", "HARM ?", "RSLP ?",
                    "ISRC ?", "IGND ?", "ICPL ?", "ILIN ?", "SENS ?", "RMOD ?", "OFLT ?",
                    "OFSL ?", "SYNC ?", "DDEF ? 1", "DDEF ? 2", "OEXP ? 1", "OEXP ? 2",
                    "OEXP ? 3", "SRAT ?", "SEND ?", "TSTR ?", "LOCL ?"]

# Instrument snapshot: X, Y, theta, Aux In 3, Aux In 4, reference frequency
# (one SNAP), plus the phase setting - the failed front-panel knob drifts the
# last-set reference parameter, which is now the phase
SNAP_QUERY = "SNAP ? 1,2,4,7,8,9"
SNAP_NAMES = ["X (V)", "Y (V)", "theta (deg)", "Aux In 3 raw (V)", "Aux In 4 (V)", "Ref freq (Hz)"]
SNAPSHOT_NAMES = SNAP_NAMES + ["Phase setting (deg)"]

# LIAS? latched status bits (cleared on read)
LIAS_BITS = ["INPUT_OVLD", "FILTER_OVLD", "OUTPUT_OVLD", "UNLOCK", "RANGE_SWITCH", "TC_CHANGE", "TRIGGERED"]


def decode_lias(value):
    return "|".join(name for bit, name in enumerate(LIAS_BITS) if value & (1 << bit))


class RawDebugRecorder:
    """SRS830_DEBUG_RAW: writes debugging data to <run_dir>/raw/. Files are
    created on first use, so each capture mode only produces its own:
      instrument.json   settings read back at every (re)connect
      snapshots.csv     streaming mode: periodic instrument snapshots
      bursts.csv / bursts_index.csv   burst mode: every sample + per-burst summary
    Write failures disable the recorder rather than disturbing acquisition."""

    def __init__(self, run_dir, time_origin):
        self.dir = os.path.join(run_dir, "raw")
        self.time_origin = time_origin
        self.n = 0
        self.enabled = True
        self.created = set()
        try:
            os.makedirs(self.dir, exist_ok=True)
        except OSError as err:
            self._disable(err)

    def _disable(self, err):
        logger.error(f"Debug raw capture disabled: {err}")
        self.enabled = False

    def _append(self, name, header, text):
        path = os.path.join(self.dir, name)
        with open(path, "a") as f:
            if name not in self.created:
                self.created.add(name)
                if f.tell() == 0:
                    f.write(header + "\n")
            f.write(text)

    def _status_cells(self, values, lias):
        values = list(values) if values is not None else [""] * len(SNAPSHOT_NAMES)
        return values + ["" if lias is None else lias, "" if lias is None else decode_lias(lias)]

    def write_settings(self, settings):
        if not self.enabled:
            return
        try:
            with open(os.path.join(self.dir, "instrument.json"), "w") as f:
                json.dump(settings, f, indent=2)
        except OSError as err:
            self._disable(err)

    def add_snapshot(self, now, values, lias, buffer_points):
        if not self.enabled:
            return
        row = [f"{now - self.time_origin:.3f}",
               datetime.datetime.fromtimestamp(now).isoformat(timespec="milliseconds"),
               buffer_points] + self._status_cells(values, lias)
        header = ",".join(["t (s)", "local time", "buffer points"] + SNAPSHOT_NAMES + ["LIAS", "LIAS flags"])
        try:
            self._append("snapshots.csv", header, ",".join(str(v) for v in row) + "\n")
        except OSError as err:
            self._disable(err)

    def add_burst(self, start_time, timebase, data_r, ch2_raw, values=None, lias=None):
        if not self.enabled:
            return
        self.n += 1
        n = len(data_r) if ch2_raw is None else min(len(data_r), len(ch2_raw))
        ch2 = np.full(n, np.nan) if ch2_raw is None else np.asarray(ch2_raw[:n])
        r = np.asarray(data_r[:n])
        upper = r[r > r.mean() + r.std() / 2] if n else r
        row = [self.n, f"{start_time - self.time_origin:.3f}",
               datetime.datetime.fromtimestamp(start_time).isoformat(timespec="milliseconds"), n,
               f"{r.mean():.7g}" if n else "", f"{np.median(upper):.7g}" if len(upper) else "",
               f"{np.mean(ch2):.7g}" if n and ch2_raw is not None else ""]
        row += self._status_cells(values, lias)
        index_header = ",".join(["burst", "t (s)", "start (local time)", "points", "R mean (V)",
                                 "R upper median (V)", "CH2 raw mean (V)"] + SNAPSHOT_NAMES + ["LIAS", "LIAS flags"])
        buf = io.StringIO()
        np.savetxt(buf, np.column_stack([np.full(n, self.n), timebase[:n], r, ch2]),
                   fmt=["%d", "%.6f", "%.7g", "%.7g"], delimiter=",")
        try:
            self._append("bursts.csv", "burst,t in burst (s),R (V),CH2 raw (V)", buf.getvalue())
            self._append("bursts_index.csv", index_header, ",".join(str(v) for v in row) + "\n")
        except OSError as err:
            self._disable(err)


def append_raw_stream(fname, t, r, ch2):
    # Continuous-capture raw log: absolute time (s since epoch), R (V), CH2 (V, sign-corrected)
    new = not os.path.exists(fname)
    with open(fname, "a") as f:
        if new:
            f.write("Time (s since epoch),R (V),CH2 (V)\n")
        np.savetxt(f, np.column_stack([t, r, ch2]), fmt=["%.4f", "%.7g", "%.7g"], delimiter=",")


class SRS830Handler:
    def __init__(self, data_queue, command_queue, run_name, run_dir, serial_port=None):
        self.p = threading.Thread(target=self.run, daemon=True)
        self.logger = logging.getLogger("RTLR.srs830.SRS830Handler")
        self.run_name = run_name
        self.run_dir = run_dir
        self.queue_data_out = data_queue
        self.queue_commands_in = command_queue
        self.state = common.SRS830_STATE_INIT

        # Serial Port parameters
        self.serialPort = serial_port
        self.ser = None
        self.last_connect_attempt = None

        # Burst start times are reported relative to this (the GUI adopts it
        # as its own time origin, so raw debug files line up with the
        # reflectance file exactly)
        self.time_origin = time.time()
        self.last_ch2_raw = None  # CH2 as the instrument reported it (before SRS830_CH2_SIGN)
        self.debug = RawDebugRecorder(run_dir, self.time_origin) if common.SRS830_DEBUG_RAW else None
        self.debug_extras = True  # per-burst SNAP/LIAS; switched off if they keep failing
        self.debug_failures = 0
        self.debug_last_snapshot = float("-inf")

        # Read by the GUI thread (plain attribute reads, atomic in CPython).
        # connected_port is set once *IDN? verifies an SR830 on that port.
        self.connected_port = None
        self.status = "Waiting for serial port" if serial_port is None else f"Connecting to {serial_port}"

        if common.SRS830_FAKE_SERIAL:
            self.state = common.SRS830_STATE_RUN_CAPTURING_DATA  # Skip Init
            self.fake_level = 1.0
            self.status = "Fake serial data (no instrument)"

        # Flags
        self.event_end = threading.Event()
        self.flagSerialError = False
        self.flagCloseSerialPort = False

        # Start the thread!
        self.i = 0
        self.p.start()

    def stop(self):
        # Thread-safe; wakes the worker out of any wait immediately.
        self.event_end.set()

    def wait(self, seconds):
        # Sleep that aborts as soon as stop() is called.
        self.event_end.wait(seconds)

    def connect_and_configure(self):
        self.logger.info(f"Attempting to connect to serial port {self.serialPort}")
        self.ser = serial.Serial(self.serialPort,
                                 timeout=common.SRS830_TIMEOUT_S,
                                 baudrate=common.SRS830_BAUD)
        send_command(self.ser, "OUTX 0")  # Respond over RS232
        # Drop any half-sent reply left over from before a reconnect - stray
        # binary buffer bytes would otherwise corrupt the *IDN response.
        self.wait(0.2)
        self.ser.reset_input_buffer()
        send_command(self.ser, "*IDN ?")
        res = capture_until_eol(self.ser)
        if "SR830" not in res.decode("utf-8", errors="replace"):
            raise SRS830Error(f"Unexpected *IDN response: {res}")

        self.logger.info("Communication with SRS830 verified! Configuring instrument.")
        # No *RST: it resets the front-panel reference display to FREQ, and the
        # failed knob drifts whatever that display shows. With the display left
        # on [Phase] the drift only moves the phase, which R ignores. Instead,
        # every setting *RST would have covered is set explicitly here.
        send_command(self.ser, "FMOD 1")  # Internal frequency reference
        send_command(self.ser, "HARM 1")  # Detect at the fundamental
        send_command(self.ser, "RSLP 0")  # Ref trigger slope (unused with internal ref)
        send_command(self.ser, "ILIN 0")  # Line notch filters out
        send_command(self.ser, "SYNC 0")  # Synchronous filter off
        send_command(self.ser, "OFSL 1")  # 12 dB/oct output filter
        for ch in (1, 2, 3):
            send_command(self.ser, f"OEXP {ch},0,0")  # No offset or expand on X, Y, R
        send_command(self.ser, f"FREQ {common.SRS830_REF_FREQ_HZ}")
        send_command(self.ser, f"SLVL {common.SRS830_AMPLITUDE_VRMS}")
        send_command(self.ser, f"ISRC {common.SRS830_ISRC_CODE}")  # Input configuration
        send_command(self.ser, "IGND 1")  # Ground the PD
        send_command(self.ser, "ICPL 0")  # AC couple the input
        send_command(self.ser, "RMOD 1")  # Normal reserve
        send_command(self.ser, f"SENS {common.SRS830_SENS_CODE}")
        send_command(self.ser, f"OFLT {common.SRS830_OFLT_CODE}")
        send_command(self.ser, "DDEF 1,1,0")  # Set ch1 display to R
        send_command(self.ser, f"DDEF {common.SRS830_DDEF_CH2}")  # Ch2 display source (theta or aux input)
        srat = common.SRS830_STREAM_SRAT_CODE if common.SRS830_STREAM else common.SRS830_SRAT_CODE
        send_command(self.ser, f"SRAT {srat}")
        send_command(self.ser, "SEND 0")  # Single shot capture
        send_command(self.ser, "TSTR 0")  # Disable hardware trigger
        send_command(self.ser, "PHAS 0")
        # Remote mode: front panel (incl. the drifting knob) is disabled while
        # the program runs, but the Local key still works as an escape hatch.
        send_command(self.ser, "LOCL 1")
        self.wait(common.SRS830_SETTLE_TIME_S)  # Wait for settings to take effect
        if self.debug is not None:
            self.record_instrument_settings()

    def debug_query(self, command):
        """Query for the debug recorder only. An odd reply or timeout must
        never stop acquisition: flush the line, log, and return None."""
        try:
            return query(self.ser, command)
        except SRS830TimeoutError as err:
            self.logger.warning(f"Debug query {command!r} failed ({err}); continuing without it")
            self.ser.reset_input_buffer()
            return None

    def debug_snapshot(self):
        """(values, lias) for SNAPSHOT_NAMES, or Nones for anything that failed."""
        if not self.debug_extras:
            return None, None
        snap = self.debug_query(SNAP_QUERY)
        phas = self.debug_query("PHAS ?")
        lias = self.debug_query("LIAS ?")
        snap = snap.split(",") if snap else None
        if snap is not None and len(snap) != len(SNAP_NAMES):
            self.logger.warning(f"Unexpected SNAP reply {snap!r}")
            snap = None
        try:
            lias = int(lias) if lias is not None else None
        except ValueError:
            self.logger.warning(f"Unexpected LIAS reply {lias!r}")
            lias = None
        if snap is None and lias is None and phas is None:
            self.debug_failures += 1
            if self.debug_failures >= 3:
                self.logger.error("Debug SNAP/PHAS/LIAS failed 3 times; disabling them (data capture continues)")
                self.debug_extras = False
            return None, None
        self.debug_failures = 0
        values = (snap if snap is not None else [""] * len(SNAP_NAMES)) + [phas if phas is not None else ""]
        return values, lias

    def record_instrument_settings(self):
        config = {k: getattr(common, k) for k in dir(common)
                  if k.startswith("SRS830_") and not k.startswith(("SRS830_STATE_", "SRS830_COMMAND_"))}
        readback = {q: self.debug_query(q) for q in SETTINGS_QUERIES}
        lias = self.debug_query("LIAS ?")  # read clears the latch, so bursts start clean
        try:
            lias = int(lias)
            lias_info = [lias, decode_lias(lias)]
        except (TypeError, ValueError):
            lias_info = [lias, "unreadable"]
        self.debug.write_settings({
            "connected": datetime.datetime.now().isoformat(timespec="seconds"),
            "port": self.serialPort,
            "config_sent": config,
            "readback": readback,
            "lias_at_connect": lias_info,
            # rtlr times are (epoch - time_origin_epoch); raw_stream.csv uses epoch
            "time_origin_epoch": self.time_origin,
        })

    def transfer_data(self):
        send_command(self.ser, "SPTS ?")  # Request number of stored points
        points = int(capture_until_eol(self.ser))

        # R data:
        data_r = read_buffer(self.ser, 1, points)
        timebase = np.arange(len(data_r)) / common.SRS830_CAPTURE_RATE_HZ

        # CH2 (theta or aux input, per SRS830_DDEF_CH2) - if needed
        if common.SRS830_CAPTURE_CH2:
            self.last_ch2_raw = read_buffer(self.ser, 2, points)  # instrument sign, for the debug recorder
            data_ch2 = common.SRS830_CH2_SIGN * self.last_ch2_raw
        else:
            self.last_ch2_raw = None
            data_ch2 = np.zeros(timebase.shape)

        return timebase, data_r, data_ch2

    def refresh_reference(self):
        # Undo any knob drift and re-lock the panel. Remote writes don't change
        # which parameter the knob acts on - that follows the front-panel
        # reference display, so leave it on [Phase] (tested 2026-11-13).
        send_command(self.ser, f"FREQ {common.SRS830_REF_FREQ_HZ}")
        send_command(self.ser, "PHAS 0")
        send_command(self.ser, "LOCL 1")

    def stream_start(self):
        # (Re)start continuous storage from an empty buffer
        send_command(self.ser, "PAUS")
        self.refresh_reference()
        send_command(self.ser, "REST")
        send_command(self.ser, "STRT")
        self.stream_t0 = time.time()
        self.stream_nread = 0
        self.stream_pending = (np.empty(0), np.empty(0))
        self.stream_block_start = 0  # sample index of the first pending sample

    def stream_read_new(self):
        send_command(self.ser, "SPTS ?")
        n = int(capture_until_eol(self.ser))
        k = n - self.stream_nread
        if k <= 0:
            return
        r = read_buffer(self.ser, 1, k, start=self.stream_nread)
        if common.SRS830_CAPTURE_CH2:
            ch2 = common.SRS830_CH2_SIGN * read_buffer(self.ser, 2, k, start=self.stream_nread)
        else:
            ch2 = np.zeros(k)
        if common.SRS830_STREAM_SAVE_RAW:
            t = self.stream_t0 + (self.stream_nread + np.arange(k)) / common.SRS830_STREAM_RATE_HZ
            append_raw_stream(os.path.join(self.run_dir, "raw_stream.csv"), t, r, ch2)
        self.stream_nread = n
        self.stream_pending = (np.concatenate([self.stream_pending[0], r]), np.concatenate([self.stream_pending[1], ch2]))

    def stream_emit(self, flush=False):
        # Hand complete blocks (and on flush, a final block of at least half
        # length) to the GUI in the same shape as a burst capture
        rate = common.SRS830_STREAM_RATE_HZ
        block = int(round(common.SRS830_STREAM_BLOCK_S * rate))
        r, ch2 = self.stream_pending
        while len(r) >= block or (flush and len(r) >= block // 2):
            m = min(block, len(r))
            start_time = self.stream_t0 + self.stream_block_start / rate
            self.queue_data_out.put([start_time, (np.arange(m) / rate, r[:m], ch2[:m])])
            r, ch2 = r[m:], ch2[m:]
            self.stream_block_start += m
        self.stream_pending = (r, ch2)

    def stream_poll(self):
        self.wait(common.SRS830_STREAM_POLL_S)
        if self.event_end.is_set():
            return
        self.stream_read_new()
        if self.debug is not None and time.monotonic() - self.debug_last_snapshot >= common.SRS830_DEBUG_SNAPSHOT_S:
            self.debug_last_snapshot = time.monotonic()
            values, lias = self.debug_snapshot()
            self.debug.add_snapshot(time.time(), values, lias, self.stream_nread)
        if self.stream_nread >= common.SRS830_STREAM_RESTART_PTS:
            send_command(self.ser, "PAUS")
            self.stream_read_new()
            self.stream_emit(flush=True)
            self.logger.info(f"Restarting stream buffer after {self.stream_nread} points")
            self.stream_start()
        else:
            self.stream_emit()

    def fake_data(self):
        # Synthesize a chopped square wave with noise so fake mode exercises the
        # full analysis/plotting/saving pipeline.
        n = int(common.SRS830_CAPTURE_TIME_S * common.SRS830_CAPTURE_RATE_HZ)
        timebase = np.arange(n) / common.SRS830_CAPTURE_RATE_HZ
        chop = np.where(np.sin(2 * np.pi * 5.0 * timebase) > 0, self.fake_level, 0.1 * self.fake_level)
        data_r = chop + np.random.normal(0, 0.01, n)
        data_ch2 = np.full(n, 0.5 + 0.1 * self.fake_level) + np.random.normal(0, 0.002, n)
        self.fake_level = max(0.2, self.fake_level + np.random.normal(0, 0.02))
        return timebase, data_r, data_ch2

    def run(self):
        self.logger.info("Starting SRS830Handler")
        start_time = time.time()

        while self.state != common.SRS830_STATE_RUN_ENDING:
            self.i += 1

            try:
                match self.state:
                    case common.SRS830_STATE_INIT:
                        self.logger.info("SRS830 thread initialized")
                        self.state = common.SRS830_STATE_WAITING_FOR_SERIAL_PORT

                    case common.SRS830_STATE_WAITING_FOR_SERIAL_PORT:
                        if self.serialPort is None:
                            if self.i % 10 == 0:
                                self.logger.info(f"Waiting for serial port definition. Thread cycle {self.i}")
                            self.wait(1)
                        elif self.last_connect_attempt is not None and \
                                time.monotonic() - self.last_connect_attempt < common.SRS830_RETRY_DELAY_S:
                            self.wait(0.5)
                        else:
                            self.last_connect_attempt = time.monotonic()
                            self.status = f"Connecting to {self.serialPort}"
                            self.connect_and_configure()
                            self.connected_port = self.serialPort
                            self.status = f"Connected to SR830 on {self.serialPort}"
                            if common.SRS830_STREAM:
                                self.stream_start()
                                self.state = common.SRS830_STATE_RUN_STREAMING
                            else:
                                self.state = common.SRS830_STATE_RUN_CAPTURING_DATA

                    case common.SRS830_STATE_RUN_CAPTURING_DATA:
                        self.logger.info(f"Capturing data, thread cycle {self.i}")
                        start_time = time.time()
                        send_command(self.ser, "REST")  # Reset buffer
                        send_command(self.ser, "STRT")  # Start data capture
                        self.wait(common.SRS830_CAPTURE_TIME_S)
                        send_command(self.ser, "PAUS")  # Pause capture
                        if not self.event_end.is_set():
                            self.state = common.SRS830_STATE_RUN_TRANSFERRING_DATA

                    case common.SRS830_STATE_RUN_TRANSFERRING_DATA:
                        self.logger.info(f"Transferring data, thread cycle {self.i}")
                        snap, lias = None, None
                        if not common.SRS830_FAKE_SERIAL:
                            if self.debug is not None:
                                # Right after PAUS, before the slow transfer
                                snap, lias = self.debug_snapshot()
                            timebase, data_r, data_ch2 = self.transfer_data()
                            ch2_raw = self.last_ch2_raw
                        else:
                            timebase, data_r, data_ch2 = self.fake_data()
                            ch2_raw = data_ch2

                        self.queue_data_out.put([start_time, (timebase, data_r, data_ch2)])

                        if self.debug is not None:
                            self.debug.add_burst(start_time, timebase, data_r, ch2_raw, snap, lias)

                        # Back to capturing
                        self.state = common.SRS830_STATE_RUN_CAPTURING_DATA

                    case common.SRS830_STATE_RUN_STREAMING:
                        self.stream_poll()

                    case _:
                        self.logger.error(f"Fallback case hit for state {self.state}, killing srs830 thread")
                        self.event_end.set()

            except (serial.SerialException, OSError, ValueError, SRS830Error) as err:
                self.logger.error(f"Serial error in state {self.state}: {err}")
                self.connected_port = None
                self.status = f"Serial error on {self.serialPort}: {err} - retrying"
                self.flagSerialError = True

            # Handle any pending commands
            try:
                command, payload = self.queue_commands_in.get_nowait()
                self.logger.info(f"Received command {command}")
            except queue.Empty:
                command = None
                payload = None

            match command:
                case None:
                    pass

                case common.SRS830_COMMAND_RAISE_END_FLAG:
                    self.logger.info(f"Raising the end flag on iteration {self.i}")
                    self.event_end.set()

                case common.SRS830_COMMAND_SET_SERIAL_PORT:
                    if self.state == common.SRS830_STATE_WAITING_FOR_SERIAL_PORT:
                        self.serialPort = payload
                        self.last_connect_attempt = None
                        self.status = f"Connecting to {self.serialPort}"
                        self.logger.info(f"Setting serial port to {self.serialPort}")

                case common.SRS830_COMMAND_CLOSE_SERIAL_PORT:
                    self.flagCloseSerialPort = True

                case _:
                    self.logger.error(f"Error - Command not handled properly {command}")

            if self.flagSerialError:
                self.flagSerialError = False
                self.flagCloseSerialPort = True
                self.state = common.SRS830_STATE_WAITING_FOR_SERIAL_PORT

            if self.flagCloseSerialPort:
                self.flagCloseSerialPort = False
                if self.ser is not None:
                    self.ser.close()
                    self.ser = None

            if self.event_end.is_set():
                self.state = common.SRS830_STATE_RUN_ENDING

        # Done the while loop
        self.end()

    def end(self):
        self.logger.info("Ending SRS830Handler")
        if self.ser is not None:
            try:
                send_command(self.ser, "LOCL 0")  # Hand the front panel back
            except (serial.SerialException, OSError) as err:
                self.logger.warning(f"Could not return SR830 to local mode: {err}")
            self.ser.close()
            self.ser = None

    def join(self, timeout=None):
        self.p.join(timeout)
