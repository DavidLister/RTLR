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
    def __init__(self, data_queue, command_queue, serial_port=None):
        self.p = multiprocessing.Process(target=self.run)
        self.logger = logging.getLogger("RTLR.srs830.SRS830Handler")
        self.queue_data_out = data_queue
        self.queue_commands_in = command_queue
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
        self.i = 0
        self.p.start()

    def run(self):
        # todo: Finish this!
        self.logger.info("Starting SRS830Handler")

        # Ensure no variables are called before definition
        start_time = time.time()

        over = False
        while not over:
            self.i += 1
            self.logger.debug(f"Iteration {self.i}")
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
                    self.queue_data_out.put([start_time, self.i])  # Placeholder

                    self.state = common.SRS830_STATE_RUN_CAPTURING_DATA
                    # todo: if statement to raise flagSerialError

                case common.SRS830_STATE_RUN_ENDING:
                    over = True

                case _:
                    self.logger.error("Fallback case hit, killing srs830 thread")

            command = None
            if not self.queue_commands_in.empty():
                command = self.queue_commands_in.get()
                self.logger.info(f"Received command {command}")

            match command:
                case None:
                    pass

                case common.SRS830_COMMAND_RAISE_END_FLAG:
                    self.logger.info(f"Raising the end flag on iteration {self.i}")
                    self.flagEnd = True

                case common.SRS830_COMMAND_SET_SERIAL_PORT:
                    port = self.queue_commands_in.get()
                    if self.state == common.SRS830_STATE_WAITING_FOR_SERIAL_PORT and not self.serialPortDefined:
                        self.serialPort = port
                        self.logger.info(f"Setting serial port to {self.serialPort}")
                        self.serialPortDefined = True

                case common.SRS830_COMMAND_CLOSE_SERIAL_PORT:
                    self.flagCloseSerialPort = True

                case _:
                    logger.error(f"Error - Command not handled properly {command}")

            if self.flagEnd:
                self.logger.info("Changing state to ending")
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
        self.queue_data_out.close()
        self.queue_commands_in.close()
        self.p.close()

    def join(self, timeout=None):
        self.p.join(timeout)