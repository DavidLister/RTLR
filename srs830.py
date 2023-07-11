# srs830.py
#
# Interface for the SRS830
#
# Paradigm is to take a burst sample, then pause sampling to transfer the data, alternating between burst and transfer.
# Another thread handles any analysis, this library only configures the instrument and handles data transfer.
#
# David Lister
# July 2023

##import multiprocessing
import os
import time
import logging
import datetime
import serial
import threading
import common
import numpy as np

logger = logging.getLogger("RTLR.srs830")


def send_command(con, command):
    if not common.SRS830_FAKE_SERIAL:
        con.write(bytes(command + "\r\n", encoding="utf-8"))


def capture_until_eol(con, timeout_s=1):
    dout = bytes()

    over = False
    while not over:
        if con.in_waiting >= 1:
            data = con.read(1)
            if data == bytes('\r', encoding="utf-8"):
                over = True
            else:
                dout = dout + data
        else:
            wait_start = time.time()
            waiting = True
            while waiting:
                if con.in_waiting >= 1:
                    waiting = False
                if time.time() - wait_start >= timeout_s:
                    waiting = False
                    over = True

    return dout


def save_csv(t, r, theta, fname):
    out = "Time (s),R (V), Theta (degrees)\n"
    n = min(len(r), len(t), len(theta))  # Sometimes theta has fewer data points
    for i in range(len(t)):
        out = out + f"{t[i]},{r[i]},{theta[i]}\n"

    with open(fname, 'w') as f:
        f.write(out)



class SRS830Handler:
    def __init__(self, data_queue, command_queue, run_name, run_dir, serial_port=None):
##        self.p = multiprocessing.Process(target=self.run)
        self.p = threading.Thread(target=self.run)
        self.logger = logging.getLogger("RTLR.srs830.SRS830Handler")
        self.run_name = run_name
        self.run_dir = run_dir
        self.queue_data_out = data_queue
        self.queue_commands_in = command_queue
        self.state = common.SRS830_STATE_INIT
        
        # Serial Port parameters
        self.serialPort = serial_port
        self.serialPortDefined = False
        if self.serialPort is not None:
            self.serialPortDefined = True

        self.ser = None
        if common.SRS830_FAKE_SERIAL:
            self.state = common.SRS830_STATE_RUN_CAPTURING_DATA  # Skip Init

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
        capture_name = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

        over = False
        while not over:
            self.i += 1
##            self.logger.debug(f"Iteration {self.i}")
##            self.logger.debug(f"Current state is {self.state}")

            match self.state:
                case common.SRS830_STATE_INIT:
                    self.logger.info("SRS830 Process Initialized")
                    self.state = common.SRS830_STATE_WAITING_FOR_SERIAL_PORT

                case common.SRS830_STATE_WAITING_FOR_SERIAL_PORT:
                    if time.time() - start_time >= 10:
                        start_time = time.time()
                        self.logger.info(f"Waiting for serial port definition. Thread cycle {self.i}")
                        time.sleep(9)  # Slow down if sitting idle
                        

                    if self.serialPortDefined:
                        self.logger.info("Attempting to connect to serial port.")
                        self.serialPortDefined = False
                        self.ser = serial.Serial(self.serialPort,
                                                 timeout=common.SRS830_TIMEOUT_S,
                                                 baudrate=common.SRS830_BAUD)
                        send_command(self.ser, "OUTX 0")
                        send_command(self.ser, "*IDN ?")
                        res = capture_until_eol(self.ser)
                        if "SR830" in str(res):
                            self.logger.info("Communication with SRS830 verified! Configuring instrument.")
                            send_command(self.ser, "*RST")
                            send_command(self.ser, "FMOD 1")  # Internal Freq reference
                            send_command(self.ser, "FREQ 2345")  # Frequency 321 kHz
                            send_command(self.ser, "SLVL 5")  # Amplitude to 5V RMS
                            send_command(self.ser, "ISRC 0")  # Open ended voltage input
                            send_command(self.ser, "IGND 1")  # Ground the PD
                            send_command(self.ser, "ICPL 0")  # AC couple the input
                            send_command(self.ser, "RMOD 1")  # Normal reserve
                            send_command(self.ser, "SENS 24")  # Set gain to XmV range (higher number is higher range)
                            send_command(self.ser, "OFLT 4")  # Set time constant to 3ms (higher number is larger time constant)
                            send_command(self.ser, "DDEF 1,1,0")  # Set ch1 display to R
                            send_command(self.ser, "DDEF 2,1,0")  # Set ch2 display to theta
                            send_command(self.ser, "SRAT 13")  # Sets capture rate to 512Hz
                            send_command(self.ser, "SEND 0")  # Sets to single shot capture
                            send_command(self.ser, "TSTR 0")  # Disables hardware trigger
                            time.sleep(5)  # Wait for settings to take effect
                            self.state = common.SRS830_STATE_RUN_CAPTURING_DATA

                        else:
                            self.logger.warning("Could not verify serial port connection. Closing connection.")
                            self.ser.close()
                            self.ser = None

                    # todo: configure SRS830
                    # todo: verify configuration
                    # todo: move on to capturing data if it's okay

                case common.SRS830_STATE_RUN_CAPTURING_DATA:
                    self.logger.info(f"Capturing data, thread cycle {self.i}")
                    start_time = time.time()
                    capture_name = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                    send_command(self.ser, "REST")  # Reset buffer
                    send_command(self.ser, "STRT")  # Start data capture
                    self.state = common.SRS830_STATE_RUN_CAPTURING_DATA
                    # Wait for data to be captured
                    time.sleep(common.SRS830_CAPTURE_TIME_S)
                    send_command(self.ser, "PAUS")  # Pause capture
                    self.state = common.SRS830_STATE_RUN_TRANSFERRING_DATA


                case common.SRS830_STATE_RUN_TRANSFERRING_DATA:
                    self.logger.info(f"Transferring data, thread cycle {self.i}")
                    if not common.SRS830_FAKE_SERIAL:
                        send_command(self.ser, "SPTS ?")  # Request number of stored points
                        points = int(capture_until_eol(self.ser))

                        # R data:
                        send_command(self.ser, f"TRCA ? 1, 0, {points}")
                        data_r = capture_until_eol(self.ser)
                        data_r = np.array([float(d) for d in str(data_r, encoding='utf-8').split(',')[:-1]])
                        timebase = np.array([n / common.SRS830_CAPTURE_RATE_HZ for n in range(len(data_r))])

                        # Theta - if needed
                        if common.SRS830_CAPTURE_PHASE:
                            send_command(self.ser, f"TRCA ? 2, 0, {points}")
                            data_theta = capture_until_eol(self.ser)
                            data_theta = np.array([float(d) for d in str(data_theta, encoding='utf-8').split(',')[:-1]])

                        else:
                            data_theta = np.zeros(timebase.shape)
                        
                        
                        

                        # Put data to queue
                        self.queue_data_out.put([start_time, (timebase, data_r, data_theta)])

                        # Save data
                        if common.SRS830_SAVE_EACH_CAPTURE:
                            fname = str(self.i) + "--" + datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                            save_csv(timebase, data_r, data_theta, os.path.join(self.run_dir, fname))

                    else:
                        if not self.queue_data_out.empty():
                            logger.warning(f"Queue is not empty, data could be accumulating! Queue size is {self.queue_data_out.qsize()}")
                        self.queue_data_out.put([start_time, (self.i, self.i, self.i)])

                    # Back to capturing
                    self.state = common.SRS830_STATE_RUN_CAPTURING_DATA


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
                self.ser.close()

        # Done the while loop
        self.end()

    def end(self):
        self.logger.info("Ending SRS830Handler")
        if self.ser is not None:
            self.ser.close()
##        self.queue_data_out.close()
##        self.queue_commands_in.close()
##        self.p.close()

    def join(self, timeout=None):
        self.p.join(timeout)
