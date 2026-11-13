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

# SRS830
SRS830_CAPTURE_TIME_S = 1  # s - ASCII transfer is ~7 ms/point at 19200 baud, so 1 s gives a point every ~4.5 s (~8 s with CH2)
SRS830_STATE_INIT = "SRS830_STATE_INIT"
SRS830_STATE_WAITING_FOR_SERIAL_PORT = "SRS830_STATE_WAITING_FOR_SERIAL_PORT"
SRS830_STATE_RUN_CAPTURING_DATA = "SRS830_STATE_RUN_CAPTURING_DATA"
SRS830_STATE_RUN_TRANSFERRING_DATA = "SRS830_STATE_RUN_TRANSFERRING_DATA"
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
SRS830_SAVE_EACH_CAPTURE = False

# CH2 capture: records the SR830 channel-2 buffer alongside R and adds a CH2
# column to Reflectance.csv. What CH2 physically is depends on SRS830_DDEF_CH2:
# "2,1,0" = theta (default), "2,3,0" = Aux In 3, "2,4,0" = Aux In 4 (DC
# photodiode / blackbody channel lives on an aux input).
SRS830_CAPTURE_CH2 = False
SRS830_DDEF_CH2 = "2,1,0"

# Instrument configuration - single source of truth for experiment parameters
SRS830_REF_FREQ_HZ = 2345  # Internal reference frequency
SRS830_AMPLITUDE_VRMS = 1  # Sine output amplitude (4.33 Vrms is the max before saturation on the SFU MOCVD system)
SRS830_ISRC_CODE = 1  # Input: 0 = A (single-ended), 1 = A-B (differential, as wired)
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
