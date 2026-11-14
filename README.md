# RTLR
Real Time Laser Reflectometry (RTLR)

Captures burst samples from an SRS SR830 lock-in amplifier over RS232, calculates
reflectance from the chopped signal, and plots both in real time. An optional
analysis panel fits the interference fringes live for growth rate, thickness,
roughness, and a live pyrometer temperature from the CH2 photodiode channel.

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

During a run, type process events (setpoint changes, thermocouple readings,
valve changes) into the event field under the plots and press Enter. Each is
written with its time to `<timestamp>---<name>_events.csv`, on the same clock
as the reflectance file, and marked on the plot.

## Pyrometer

The top bar shows a live temperature from CH2 (Aux In 3) once logging starts.
The latest 1 s value is followed by the mean ± sd over the last 60 s, which is
the number to compare between runs on a bake plateau. Hover over it for the
constants in use.

How it works (`pyrometer.py`, settings `PYRO_*` in `common.py`):
- **Laser leak:** the laser's own DC light reaches CH2 in proportion to R, so
  the thermal signal is S = CH2 − a − g·mean(R). Typically g ≈ 6.4 at SLVL 1.
  - g is measured automatically from the first 60 s of cold, laser-on data, so
    start logging cold with the laser on. **Cal leak** in the analysis panel
    re-measures it from the last 30 s (cold only).
  - The dark offset a is updated from cold laser-off or beam-blocked stretches.
- **The thermal light is near-IR, not 650 nm:** the 650 nm bandpass (Newport
  10BPF10-650) doesn't block ~1 µm light. So S follows Wien at an effective
  wavelength of 1.21 µm, fitted on S432 against the thermocouple. No
  emissivity correction is applied, because the 650 nm R doesn't describe
  near-IR light. On sapphire the reading is mostly the susceptor seen through
  the wafer.
- **Fixed scale:** every run is reported on the S432 scale (S = 1.090 V reads
  1000.9 °C), so plateaus can be compared between runs. **Set T ref** in the
  analysis panel re-pins the scale for one run, to the temperature in the box.
- **Range:** blank below ~650 °C, where the leak uncertainty dominates. At
  1000 °C the 1 s noise is ~0.15 K.
- **A beam block while hot** gives a leak-free reading, since S = CH2 − a.
- **Throughput:** a 1% change in optical throughput (alignment, window
  coating) shifts the reading by ~1.4 K at 1020 °C. Compare the cold R on bare
  sapphire between runs.
- **Thermocouple lag:** the thermocouple lags the pyrometer by ~20 s on ramps.
- **GaN films:** see the analysis-panel caveats below.
- **Output:** every block is written to `<name>_pyrometer.csv` (R mean, CH2,
  laser on, S, T, a, g).

When the laser is off or the beam is blocked (mean R < 1 mV), the log says so
once, and reflectance points pause until it's back. CH2 is still recorded.

Offline, the same calculation runs on any recorded run, and can line it up with
the reactor datalog (thermocouple = AI56):

```
python pyro_check.py DATA/<run> --datalog "<reactor datalog>.csv" --offset 147 --plot out.png
```

`--offset` is how far the reactor PC's clock is ahead of the RTLR PC, in
seconds. Measure it each run, because the reactor clock drifts. The script
prints the leak constants, the cold R, and every steady plateau with the
pyrometer − thermocouple difference.

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
- `SRS830_DEBUG_RAW = True` keeps every raw burst sample plus per-burst
  instrument snapshots (X, Y, θ, Aux In 3/4, ref freq, overload/unlock
  status) and the read-back instrument settings in `<run_dir>/raw/` —
  for debugging the R and pyrometer channels offline (~15 MB per 2 h)
- `SRS830_FAKE_SERIAL = True` runs with synthesized data, no instrument needed

Data is saved as `DATA/<timestamp>---<name>/<timestamp>---<name>_Reflectance.csv`
(plus `Analysis.csv` / `segments.json` if the analysis panel is used).

## Analysis panel

Click the **Analysis** caret under the plots (the fitting engine only loads
when first opened). Workflow during a run:

1. Select the substrate (Al2O3 / Si), set the temperature, press
   **Calibrate R** while the laser is on the bare substrate — this anchors
   volts to absolute reflectance.
2. The panel's T readout is the live pyrometer (see above). **Set T ref**
   re-pins its scale for this run, and **Cal leak** re-measures the laser leak
   while cold.
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
- The CH2 thermal signal is near-IR (~1.2 µm effective), so ε = 1 − R(650 nm)
  can't correct it. A GaN film changes how much susceptor light gets through.
  - A thin-film model predicts the reading swings between 0 and about −28 K
    for the first ~0.5 µm, then settles to about −15 K once the film is
    thicker than ~0.7 µm, as the broad detection band averages out the
    interference.
  - S403 shows a much weaker, smooth effect, and rough films (S432) show
    almost none.
  - Treat temperatures during growth as relative until this is calibrated
    against a run with the thermocouple logged. Bake readings on bare
    sapphire are unaffected.
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
