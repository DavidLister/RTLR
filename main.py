# main.py
#
# Main for the Real Time Laser Reflectometry (RTLR) program.
# Opens GUI window and threads for interfacing with the lock-in amplifier.
#
# David Lister
# July 2023
#

import logging
import multiprocessing

import srs830


logger = logging.getLogger("RTLR")
logger.setLevel(logging.DEBUG)
ch = logging.StreamHandler()
ch.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
ch.setFormatter(formatter)
logger.addHandler(ch)

logger.debug("Logger Started")

if __name__ == "__main__":
    queue_srs_to_analysis = multiprocessing.SimpleQueue()
    queue_analysis_to_handler = multiprocessing.SimpleQueue()

    srs830_handler = srs830.SRS830Handler("Port4", queue_srs_to_analysis)
    print("Done!")