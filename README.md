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

Configuration lives in `common.py`:
- `SRS830_COM_PORT` — serial port the SR830 is connected to (e.g. `COM4`)
- `SRS830_CAPTURE_TIME_S` — burst capture length
- Instrument settings (reference frequency, sensitivity, time constant, etc.)
- `SRS830_CAPTURE_CH2` — also record the SR830 channel-2 buffer (theta or an
  aux input per `SRS830_DDEF_CH2`, e.g. the DC photodiode for pyrometry) as a
  `CH2` column in `Reflectance.csv`
- `SRS830_FAKE_SERIAL = True` runs with synthesized data, no instrument needed

Data is saved under `DATA/<timestamp>---<run info>/`.

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
    - Gui to start/stop capture, choose file names and paths
    - Merge remote fork's CH2/SRS830 config, then set SRS830_DDEF_CH2 to match
    - Dedicated GaN etch calibration run to anchor the etch-rate thermometer
