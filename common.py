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
SRS830_CAPTURE_TIME_S = 10  # s
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

SRS830_COM_PORT = "COM4"
SRS830_BAUD = 19200
SRS830_TIMEOUT_S = 5  # Inter-byte timeout - only trips after the line goes silent this long mid-response
SRS830_RETRY_DELAY_S = 5  # Delay between serial connection attempts
SRS830_SETTLE_TIME_S = 5  # Wait after configuring the instrument
SRS830_SAVE_EACH_CAPTURE = False
SRS830_CAPTURE_PHASE = False

# Instrument configuration - single source of truth for experiment parameters
SRS830_REF_FREQ_HZ = 2345  # Internal reference frequency
SRS830_AMPLITUDE_VRMS = 5  # Sine output amplitude
SRS830_SENS_CODE = 24  # Sensitivity: 24 = 200 mV (higher number is higher range)
SRS830_OFLT_CODE = 4  # Time constant: 4 = 1 ms (higher number is larger time constant)
SRS830_SRAT_CODE = 13  # Sample rate code: 13 = 512 Hz, must match SRS830_CAPTURE_RATE_HZ
SRS830_CAPTURE_RATE_HZ = 512

SRS830_FAKE_SERIAL = False
