# srs830_testing.py
#
# Program to test the srs830 serial interface.
# David Lister
# July 2023
#

import matplotlib.pyplot as plt
import numpy as np
import serial
import time

COM_PORT = "COM4"
BAUD = 19200
TIMEOUT_S = 5
DEBUG = True
CAPTURE_RATE_HZ = 512

def send_command(con, command):
    con.write(bytes(command + "\r\n", encoding="utf-8"))



def capture_until_eol(con, wait=True, timeout_s=1):
    dout = bytes()
    cr_flag = False
    lf_flag = False
        
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

    if DEBUG:
        print(dout)

    return dout


def save_csv(t, r, theta, fname):
    out = "Time (s),R (V), Theta (degrees)\n"
    for i in range(len(t)):
        out = out + f"{t[i]},{r[i]},{theta[i]}\n"

    with open(fname, 'w') as f:
        f.write(out)


def capture_data(ser, capture_time_s):
    send_command(ser, "REST")  # Reset buffer
    send_command(ser, "STRT")  # Start data capture
    time.sleep(capture_time_s) # Capture for defined time
    send_command(ser, "PAUS")  # Pause capture

    send_command(ser, "SPTS ?") # Request number of stored points
    points = int(capture_until_eol(ser))

    # Transfer data
    send_command(ser, f"TRCA ? 1, 0, {points}")
    data_r = capture_until_eol(ser)
    send_command(ser, f"TRCA ? 2, 0, {points}")
    data_theta = capture_until_eol(ser)

    # Process data
    data_r = [float(d) for d in str(data_r, encoding='utf-8').split(',')[:-1]]
    data_theta = [float(d) for d in str(data_theta, encoding='utf-8').split(',')[:-1]]
    timebase = [n/CAPTURE_RATE_HZ for n in range(len(data_r))]

    return timebase, data_r, data_theta
    


if __name__ == "__main__":
    ser = serial.Serial(COM_PORT, timeout=TIMEOUT_S, baudrate=BAUD)

    send_command(ser, "OUTX 0")
    send_command(ser, "*IDN ?")
    res = capture_until_eol(ser)
    if "SR830" in str(res):
        print("Comunication with SRS830 verified")

    send_command(ser, "*RST")
    
    send_command(ser, "FMOD 1")  # Internal Freq reference
    send_command(ser, "FREQ 321")  # Frequency 14.123 kHz
    send_command(ser, "SLVL 5")  # Amplitude to 5V RMS
    send_command(ser, "ISRC 0")  # Open ended voltage input
    send_command(ser, "IGND 1")  # Ground the PD
    send_command(ser, "ICPL 0")  # AC couple the input
    send_command(ser, "RMOD 1")  # Normal reserve
    send_command(ser, "SENS 22")  # d=21 Set gain to 20mV range (higher number is higher range)
    send_command(ser, "OFLT 6")  # d=5 Set time constant to 3ms (higher number is larger time constant)
    send_command(ser, "DDEF 1,1,0")  # Set ch1 display to R
    send_command(ser, "DDEF 2,1,0")  # Set ch2 display to theta
    send_command(ser, "SRAT 13")  # Sets capture rate to 512Hz
    send_command(ser, "SEND 0")  # Sets to single shot capture
    send_command(ser, "TSTR 0")  # Disables hardware trigger
    time.sleep(5)  # Wait for settings to take effect

    t, r, theta = capture_data(ser, 3)

    save_csv(t, r, theta, "Series_2_100rpm_realigment_2_with_mask_5_normal_reserve_sens22_oflt6_freq321_cover.csv")


    
    
