# RTLR
Real Time Laser Reflectometry (RTLR)

Captures burst samples from an SRS SR830 lock-in amplifier over RS232, calculates
reflectance from the chopped signal, and plots both in real time. An optional
analysis panel fits the interference fringes live for growth rate, thickness,
roughness, and emissivity-corrected temperature.

## Running

```
pip install -r requirements.txt
python rtlr.py
```

Nothing connects or logs until you:
1. Pick the serial port from the dropdown (COM ports on Windows, `/dev/tty*`
   on Linux; ↻ rescans, and you can type a path that isn't listed). It
   defaults to the last port where an SR830 answered, remembered across
   sessions. If the connection fails you can pick another port without
   restarting; the selector locks once the instrument answers.
2. Enter a run name (required; letters, digits, `.`, `_`, `-`, spaces become
   `_`).
3. Press **Start logging** (or Enter).

On Linux the user must be in the `dialout` group to open the port.

Configuration lives in `common.py`:
- `SRS830_COM_PORT` — fallback serial port, used until a port has worked once
- `SRS830_CAPTURE_TIME_S` — burst capture length
- Instrument settings (reference frequency, sensitivity, time constant, etc.)
- `SRS830_CAPTURE_CH2` / `SRS830_DDEF_CH2` — record the SR830 channel-2
  buffer as a `CH2` column. Default is Aux In 3, the DC photodiode used for
  pyrometry; `"2,1,0"` switches it to theta
- `SRS830_CH2_SIGN` — the Aux In 3 photodiode signal is inverted, so CH2 is
  multiplied by −1 before it is saved, plotted, or used for pyrometry
- `PLOT_CH2` — show CH2 on a right-hand axis of the reflectance plot
- `SRS830_FAKE_SERIAL = True` runs with synthesized data, no instrument needed

Data is saved as `DATA/<timestamp>---<name>/<timestamp>---<name>_Reflectance.csv`
(plus `Analysis.csv` / `segments.json` if the analysis panel is used).

## Analysis panel

Click the **Analysis** caret under the plots (the fitting engine only loads
when first opened). Workflow during a run:

1. Select the substrate (Al2O3 / Si), set the temperature, press
   **Calibrate R** while the laser is on the bare substrate — this anchors
   volts to absolute reflectance.
2. Optionally **Set T ref** at a trusted temperature to enable the
   emissivity-corrected temperature readout (needs the CH2 DC channel).
3. Select the material and press **Dep start** / **Dep end** (and
   **Etch start** / **Etch end** around anneals). Segments are shaded on the
   plot; green = deposition, orange = etch.

Live readouts: layer thickness, growth/etch rate ± uncertainty (µm/hr), and
roughness with a two-tier alarm — amber `⚠ decay` (fringe peaks decaying
abnormally fast) and red `⚠ ROUGH` (specular envelope collapse confirmed).
Etch segments report a planar etch rate until the signal drops below the
bare-substrate level (roughening onset), after which no rate is claimed.
Results stream to `Analysis.csv` and `segments.json` in the run directory.

The fit is a Breiland-style virtual-interface model: the buried stack is one
complex reflectance re-anchored at every segment change, so errors do not
accumulate across layers. Optical constants live in `materials.py` (650 nm,
linear temperature correction). Physics caveats worth remembering:

- Thickness/rate need visible fringes: after washout the readout freezes and
  flags rather than guessing.
- On sapphire the 650 nm radiometric channel reads an effective pocket-weighted
  temperature (wafer + susceptor); ε = 1 − R pyrometry is only strictly valid
  on opaque substrates (Si).
- The GaN etch-rate thermometer (`analysis.etch_rate_temperature`) uses a
  literature prefactor — calibrate with a dedicated NH3-off etch run before
  trusting absolute temperatures from it.

## Offline analysis and replay

Re-analyze any recorded run (also useful for tuning detector thresholds —
they are constants at the top of `analysis.py`):

```
python analysis.py DATA/<run_dir> --segments segments.json --plot
```

Replay a recorded run through the full live GUI at any speed:

```
python rtlr.py TempData/S195_2024-02-06_RTLR/Reflectance.csv 20
```

Note: at very high replay speeds the fitter can lag the data stream; at
real-time rates it has ~80x headroom.

## Tests

```
pytest tests/                    # synthetic ground-truth suite
python tests/gui_harness.py      # scripted GUI run over a replay, screenshots + assertions
```

# Running Todo
    - GUI stop/restart of a capture without restarting the program, and choice of save path
    - Dedicated GaN etch calibration run to anchor the etch-rate thermometer
