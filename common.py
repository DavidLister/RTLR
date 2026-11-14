# common.py
#
# Area for configuration variables and commonly-used values
# David Lister
# July 2023
#

# General purpose
DATA_SUBPATH = "DATA"
SAVE_CALCULATED_REFLECTANCE = True

# Types of reflectance calculations
CALC_PEAK_TO_PEAK = "CALC_PEAK_TO_PEAK"
CALC_UPPER_MEDIAN = "CALC_UPPER_MEDIAN"
CALC_TYPE = CALC_UPPER_MEDIAN

# Main Window
WINDOW_UPDATE_RATE_MS = 500  # ms
PLOT_CH2 = True  # Plot CH2 on a right-hand axis of the reflectance plot (when captured)

# SRS830
SRS830_CAPTURE_TIME_S = 1  # s - binary (TRCB) transfer is ~2.1 ms/point at 19200 baud, so 1 s gives a point every ~2 s (~3 s with CH2)
SRS830_STATE_INIT = "SRS830_STATE_INIT"
SRS830_STATE_WAITING_FOR_SERIAL_PORT = "SRS830_STATE_WAITING_FOR_SERIAL_PORT"
SRS830_STATE_RUN_CAPTURING_DATA = "SRS830_STATE_RUN_CAPTURING_DATA"
SRS830_STATE_RUN_TRANSFERRING_DATA = "SRS830_STATE_RUN_TRANSFERRING_DATA"
SRS830_STATE_RUN_STREAMING = "SRS830_STATE_RUN_STREAMING"
SRS830_STATE_RUN_ENDING = "SRS830_STATE_RUN_ENDING"

# Commands are put on the command queue as (command, payload) tuples.
# Payload is None unless noted otherwise.
SRS830_COMMAND_RAISE_END_FLAG = "SRS830_COMMAND_RAISE_END_FLAG"
SRS830_COMMAND_SET_SERIAL_PORT = "SRS830_COMMAND_SET_SERIAL_PORT"  # Payload is a string with the port name
SRS830_COMMAND_CLOSE_SERIAL_PORT = "SRS830_COMMAND_CLOSE_SERIAL_PORT"

SRS830_COM_PORT = "/dev/ttyUSB0"  # Prolific USB-serial adapter on the Fedora lab computer (was "COM4" on Windows)
SRS830_BAUD = 19200
SRS830_TIMEOUT_S = 5  # Inter-byte timeout - only trips after the line goes silent this long mid-response
SRS830_RETRY_DELAY_S = 5  # Delay between serial connection attempts
SRS830_SETTLE_TIME_S = 5  # Wait after configuring the instrument

# Debug capture: extra data for studying the R and CH2 (pyrometer) channels
# offline, in <run_dir>/raw/ (files appear only for the mode in use):
#   instrument.json   *IDN, config sent, every setting read back, and the
#                     epoch time origin (rtlr times = epoch - origin; the
#                     streaming raw_stream.csv uses epoch seconds)
#   snapshots.csv     streaming: every SRS830_DEBUG_SNAPSHOT_S, a SNAP of X, Y,
#                     theta, Aux In 3, Aux In 4, ref freq, the phase setting
#                     (knob drift), and latched LIA status (overload/unlock)
#   bursts.csv, bursts_index.csv   burst mode: every sample (CH2 in instrument
#                     sign) and per-burst summary with the same snapshot
# Debug query failures are logged and skipped; they never stop acquisition.
SRS830_DEBUG_RAW = True
SRS830_DEBUG_SNAPSHOT_S = 1.0  # Streaming mode: instrument snapshot interval (~70 B each, ~4% of the line)

# Continuous capture: storage runs non-stop and new points are pulled with TRCB
# while it fills, instead of alternating 1 s bursts with ~2 s transfers.
# R and CH2 at 128 Hz is 1024 B/s, ~55% of the 19200-baud line (256 Hz for both
# would need 109%). 128 Hz sampling changes the burst mean by ~0.1% vs 512 Hz at
# 800 rpm (bench test 2026-11-13). Each SRS830_STREAM_BLOCK_S of samples goes to
# the GUI as one "capture", and every sample is appended to <run_dir>/raw_stream.csv.
# The 16383-point buffer (128 s at 128 Hz) is restarted every
# SRS830_STREAM_RESTART_PTS points, leaving a sub-second gap.
SRS830_STREAM = True
SRS830_STREAM_SRAT_CODE = 11  # 11 = 128 Hz
SRS830_STREAM_RATE_HZ = 128
SRS830_STREAM_BLOCK_S = 1.0
SRS830_STREAM_POLL_S = 0.5
SRS830_STREAM_RESTART_PTS = 15000
SRS830_STREAM_SAVE_RAW = True

# CH2 capture: records the SR830 channel-2 buffer alongside R and adds a CH2
# column to the reflectance file. What CH2 physically is depends on
# SRS830_DDEF_CH2: "2,1,0" = theta, "2,3,0" = Aux In 3, "2,4,0" = Aux In 4.
# The DC photodiode (pyrometry / blackbody) channel is wired to Aux In 3.
SRS830_CAPTURE_CH2 = True
SRS830_DDEF_CH2 = "2,3,0"
# The Aux In 3 photodiode signal is inverted (raw S211 bursts were all
# negative), so it's multiplied by this before saving/plotting/analysis.
# A sign flip rather than abs() so noise near zero isn't rectified into an offset.
SRS830_CH2_SIGN = -1

# Live pyrometer (pyrometer.py): temperature from CH2 after removing the laser
# leak, S = CH2 - a - g * mean(R). The 650 nm bandpass does not block the
# near-IR, so the thermal signal is ~1 um light: Wien at an effective
# wavelength fitted on S432 (2026-11-13, TC-referenced, 20 s TC lag removed).
# The reference fixes the scale so runs are comparable: S_ref volts of thermal
# signal = T_ref C. "Set T ref" in the analysis panel re-pins it for one run.
PYRO_ENABLED = True  # needs SRS830_CAPTURE_CH2 with the photodiode on Aux In 3
PYRO_LAMBDA_EFF_UM = 1.21
PYRO_REF_SIGNAL_V = 1.090   # S432 HT deposition ...
PYRO_REF_TEMP_C = 1000.9    # ... at this TC reading
PYRO_LEAK_G = 6.4           # CH2 per volt of mean R (laser DC leak); S432 cold, wanders +-10%
PYRO_DARK_V = -0.00066      # CH2 with the laser off (S432)
PYRO_LASER_OFF_R_V = 0.001  # mean R below this = laser off / beam blocked
PYRO_DARK_MAX_V = 0.005     # laser-off CH2 within this of the dark level updates it (cold only)
PYRO_AUTO_LEAK_S = 60       # measure g from this many cold, laser-on seconds at the start
PYRO_COLD_MAX_V = 0.015     # thermal signal below this counts as cold for the auto leak
PYRO_MIN_SIGNAL_V = 0.03    # no temperature below this thermal signal (~650 C; leak error dominates)

# Instrument configuration - single source of truth for experiment parameters
SRS830_REF_FREQ_HZ = 7350  # Internal reference frequency. Midway between 60 Hz harmonics; R scatter is flat 4.7-40 kHz, ~1.7x lower than at 2345 (2026-11-13)
SRS830_AMPLITUDE_VRMS = 1  # Sine output amplitude (4.33 Vrms is the max before saturation on the SFU MOCVD system)
SRS830_ISRC_CODE = 0  # Input: 0 = A (single-ended), 1 = A-B. B is unconnected; A-B read R ~15% low and noisier (2026-11-13)
SRS830_SENS_CODE = 24  # Sensitivity: 24 = 200 mV (higher number is higher range)
SRS830_OFLT_CODE = 4  # Time constant: 4 = 1 ms (higher number is larger time constant)
SRS830_SRAT_CODE = 13  # Sample rate code: 13 = 512 Hz, must match SRS830_CAPTURE_RATE_HZ
SRS830_CAPTURE_RATE_HZ = 512

SRS830_FAKE_SERIAL = False

# Replay mode: set to a recorded Reflectance.csv path (or pass on the command
# line: python rtlr.py <recorded.csv> [speed]) to feed a past run through the
# live pipeline instead of talking to the instrument.
RTLR_REPLAY_FILE = None
RTLR_REPLAY_SPEED = 20.0
