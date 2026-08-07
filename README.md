# RTLR
Real Time Laser Reflectometry (RTLR)

Captures burst samples from an SRS SR830 lock-in amplifier over RS232, calculates
reflectance from the chopped signal, and plots both in real time.

## Running

```
pip install -r requirements.txt
python rtlr.py
```

Configuration lives in `common.py`:
- `SRS830_COM_PORT` — serial port the SR830 is connected to (e.g. `COM4`)
- `SRS830_CAPTURE_TIME_S` — burst capture length
- Instrument settings (reference frequency, sensitivity, time constant, etc.)
- `SRS830_FAKE_SERIAL = True` runs with synthesized data, no instrument needed

Data is saved under `DATA/<timestamp>---<run info>/`.

# Running Todo
    - Gui to start/stop capture, choose file names and paths
    - Make a measurement state thread to track integrated thickness and to detect roughness
