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
CALC_UPPER_MEDIAN = "CALC_AVERAGE"
CALC_TYPE = CALC_UPPER_MEDIAN

# Main Window
WINDOW_UPDATE_RATE_MS = 500  # ms

# SRS830
SRS830_CAPTURE_TIME_S = 1  #s
SRS830_STATE_INIT = "SRS830_STATE_INIT"
SRS830_STATE_WAITING_FOR_SERIAL_PORT = "SRS830_STATE_WAITING_FOR_SERIAL_PORT"
SRS830_STATE_RUN_CAPTURING_DATA = "SRS830_STATE_RUN_CAPTURING_DATA"
SRS830_STATE_RUN_TRANSFERRING_DATA = "SRS830_STATE_RUN_TRANSFERRING_DATA"
SRS830_STATE_RUN_ENDING = "SRS830_STATE_RUN_ENDING"

SRS830_COMMAND_RAISE_END_FLAG = "SRS830_COMMAND_RAISE_END_FLAG"
SRS830_COMMAND_SET_SERIAL_PORT = "SRS830_COMMAND_SET_SERIAL_PORT"  # Assumes next value in the Queue is a string with the port name
SRS830_COMMAND_CLOSE_SERIAL_PORT = "SRS830_COMMAND_CLOSE_SERIAL_PORT"

SRS830_COM_PORT = "COM4"
SRS830_BAUD = 19200
SRS830_TIMEOUT_S = 5
SRS830_CAPTURE_RATE_HZ = 512
SRS830_SAVE_EACH_CAPTURE = False
SRS830_CAPTURE_PHASE = False

SRS830_FAKE_SERIAL = False
