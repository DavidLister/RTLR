# srs830.py
#
# Interface for the SRS830
#
# Paradigm is to take a burst sample, then pause sampling to transfer the data, alternating between burst and transfer.
# Another thread handles any analysis, this library only configures the instrument and handles data transfer.
#
# David Lister
# July 2023

import multiprocessing
import time
import logging

import common

logger = logging.getLogger("RTLR.srs830")


class SRS830Handler:
    def __init__(self, data_queue, serial_port=None):
        self.p = multiprocessing.Process(target=self.run)
        self.logger = logging.getLogger("RTLR.srs830.SRS830Handler")
        self.outQueue = data_queue
        self.state = common.SRS830_STATE_INIT

        # Serial Port parameters
        self.serialPort = serial_port
        self.serialPortDefined = False
        if self.serialPort is not None:
            self.serialPortDefined = True

        # Flags
        self.flagEnd = False
        self.flagSerialError = False
        self.flagCloseSerialPort = False

        # Start the process!
        self.p.start()

    def run(self):
        # todo: Finish this!
        self.logger.info("Starting SRS830Handler")

        # Ensure no variables are called before definition
        start_time = time.time()

        i = 0
        over = False
        while not over:
            i += 1
            self.logger.debug(f"Iteration {i}")
            self.logger.debug(f"Current state is {self.state}")

            match self.state:

                case common.SRS830_STATE_INIT:
                    self.state = common.SRS830_STATE_WAITING_FOR_SERIAL_PORT

                case common.SRS830_STATE_WAITING_FOR_SERIAL_PORT:
                    self.state = common.SRS830_STATE_RUN_CAPTURING_DATA  # temp
                    # todo: Check for a new serial port defined
                    # todo: try connecting to serial port
                    # todo: check that it's the correct instrument
                    # todo: configure SRS830
                    # todo: verify configuration
                    # todo: move on to capturing data if it's okay

                case common.SRS830_STATE_RUN_CAPTURING_DATA:
                    # todo: Reset buffer and start data capture
                    self.state = common.SRS830_STATE_RUN_CAPTURING_DATA
                    start_time = time.time()
                    # Wait for data to be captured
                    time.sleep(common.SRS830_CAPTURE_TIME_S)
                    # todo: pause data capture
                    self.state = common.SRS830_STATE_RUN_TRANSFERRING_DATA
                    # todo: if statement to raise flagSerialError

                case common.SRS830_STATE_RUN_TRANSFERRING_DATA:
                    # todo: Transfer data from SRS830

                    # Put data to queue
                    self.outQueue.put([start_time, i])  # Placeholder

                    self.state = common.SRS830_STATE_RUN_CAPTURING_DATA
                    # todo: if statement to raise flagSerialError

                case common.SRS830_STATE_RUN_ENDING:
                    over = True

                case _:
                    self.logger.error("Fallback case hit, killing srs830 thread")

            # Check end conditions
            if self.flagEnd:
                self.state = common.SRS830_STATE_RUN_ENDING

            if self.flagSerialError:
                self.state = common.SRS830_STATE_WAITING_FOR_SERIAL_PORT
                self.flagCloseSerialPort = True

            if self.flagCloseSerialPort:
                # todo: Close the serial port
                pass

        # Done the while loop
        self.end()

    def end(self):
        self.logger.info("Ending SRS830Handler")
        self.p.close()

    def join(self, timeout=None):
        self.p.join(timeout)

    def serial_port_close(self):
        if self.state == common.SRS830_STATE_RUN_CAPTURING_DATA or \
                self.state == common.SRS830_STATE_RUN_TRANSFERRING_DATA:
            self.flagSerialError = True
            return True
        return False

    def serial_port_add(self, port):
        if self.state == common.SRS830_STATE_WAITING_FOR_SERIAL_PORT and not self.serialPortDefined:
            self.serialPort = port
            self.logger.info(f"Setting serial port to {self.serialPort}")
            self.serialPortDefined = True
            return True
        else:
            return False

    def raise_end_flag(self):
        self.flagEnd = True
