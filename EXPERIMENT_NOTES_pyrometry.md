# Experiment notes: cold sapphire at 1000 RPM (pyrometer leakage)

TEMPORARY notes on branch `debug-raw-capture`, for the agent running the cold
test and for David. Written 2026-11-13. Delete the file once its points are
folded into the code or the lab notebook.

**Update, 15:15:** the program now defaults to continuous capture (128 Hz
streaming), input A (ISRC 0) and a 7350 Hz reference. The leak numbers below
were measured at 2345 Hz with the A−B input (which reads R about 15% low), so
expect a different g. Re-measure it; don't assume 6.

## What we already know (from S211, S403 and today's bench readings)

- **The pyrometer channel is not purely thermal.** Aux In 3 (CH2) =
  thermal + a + g·R. The laser's DC level leaks into it.
  - S211 (SLVL 5): CH2 = 1.325 × burst-mean R, correlation 0.996 over the
    whole 8600 s run. The residual is ±2 mV, so there's no thermal signal at all.
  - S403 (SLVL 1): CH2/R = 5.9–6.9 in every cold phase. During growth
    fringes, detrended CH2 follows R (slope 5.3–5.9, correlation 0.83–0.997).
    The leak is 15–35% of CH2 during growth.
  - Today on the bench (SLVL 1, cooled bake): R ≈ 0.5 mV, CH2 ≈ +3.5 mV,
    so CH2/R ≈ 7.
- **g × SLVL is nearly constant:** 1.33 × 5 = 6.6 versus about 6.2 × 1. This
  suggests a fixed DC laser bias with AC modulation proportional to SLVL
  (H1 below), but it rests on only two runs.
- **Use burst-mean R for the leak, not the upper median.**
  - CH2/R_upper drifts during a run (1.25 to 1.14 in S211); CH2/R_mean
    stays constant.
  - R_mean/R_upper per burst is 1.04–1.18, depending on the rotation wobble.
- **The DC path is slower than the lock-in R path.** In S211 the within-burst
  CH2-vs-R slope at the wobble frequency is 0.44, against 1.33 burst to
  burst, with about 6 ms lag. That points to a low-pass on the photodiode's
  DC path with a corner of roughly tens of Hz. At 1000 RPM (16.7 Hz) this
  matters inside a burst but not for burst means taken over whole rotations.
- **The cold, laser-on state is exactly what's needed to measure a and g.**
  There's no thermal term to confuse things, so this test can pin the leak
  down cleanly.

## Hypotheses this test can separate

| | What makes the leak | Prediction when SLVL changes (cold) | Prediction with SLVL at min (0.004 V) | Prediction with beam blocked |
|---|---|---|---|---|
| **H1** | Fixed DC laser bias plus AC ∝ SLVL | R ∝ SLVL; **CH2 stays roughly constant**; g ∝ 1/SLVL | R → ~0, **CH2 stays near its SLVL 1 value** (laser still on) | CH2 → a |
| **H2** | DC ∝ modulation depth (laser off at zero drive) | R ∝ SLVL **and CH2 ∝ SLVL**; g constant | R and CH2 both → a | CH2 → a |
| **H3** | Electrical pickup, not light | CH2 follows SLVL whatever the optics do | CH2 → a | **CH2 stays high** |

Expected numbers if the alignment matches S403 (SLVL 1, cold sapphire): R
around 18–20 mV, CH2 around 0.12 V, g around 6–7 against burst-mean R, and a
dark offset a of 0–16 mV (S403's residual after leak subtraction ranged from
0.1 to 16 mV). If R comes out far lower, as on today's bench (0.5 mV), check
the alignment first.

## Suggested protocol

Hold each step for **≥ 60 s** (about 60 one-second blocks when streaming).
**Log the wall-clock time of every change** (the event field does this). Keep SENS 24
and the new defaults (ISRC 0, 7350 Hz).
Stay under SLVL 4.33 Vrms, the saturation note.

1. **Baseline:** SLVL 1, 1000 RPM, 2 min.
2. **Rotation:** 0 RPM for 1 min, then 500 RPM for 1 min, then back to
   1000 RPM. If the burst-mean g is the same at all three speeds, the
   leak model doesn't depend on wobble, and stopped rotation gives the
   cleanest g.
3. **Optics versus electrical (H3):** block the beam at the laser output for
   1 min. R should go to about 0. CH2 then reads the dark offset a; if it
   stays high, the leak is electrical. If you can, also turn the room lights
   off for 1 min to check stray light.
4. **SLVL sweep (H1 versus H2):** 0.004 (min), 0.25, 0.5, 1, 2, 3 Vrms, 1 min
   each. Watch the LIAS output-overload bit at the high end.
   - The program sets SLVL only when it configures the instrument. So either
     restart with `SRS830_AMPLITUDE_VRMS` changed for each step, naming each
     run `...SLVL0p5` etc., or send `SLVL x` from a dedicated script that owns
     the port.
5. **Drift check:** back to SLVL 1, 1000 RPM, 1 min. Compare g with step 1.

## What gets recorded (debug build)

Streaming mode (the default):
- `raw_stream.csv`: every sample at 128 Hz. Epoch time, R, and CH2
  sign-corrected (×−1).
- `raw/snapshots.csv`: once a second, SNAP of X, Y, θ, Aux In 3 (instrument
  sign), Aux In 4 and ref freq, plus the **phase setting** (the knob's drift
  target now), the buffer point count, and latched LIAS overload/unlock flags.
- `raw/instrument.json`: every setting read back after configuring (now that
  there's no `*RST`, this is the record of what the instrument really had),
  plus `time_origin_epoch`. Reflectance-file and event times are epoch minus
  this origin.
- `<run>_events.csv`: typed process events on the reflectance-file clock.

Burst mode (`SRS830_STREAM = False`) writes `raw/bursts.csv` and
`raw/bursts_index.csv` instead of `raw_stream.csv`/`snapshots.csv`.

## Analysis to run on it

- **Leak fit:** fit CH2 = a + g·R_mean per SLVL level. Compare g across RPM
  steps and between steps 1 and 5. Plot g × SLVL against SLVL: flat means H1.
- **Take the FFT of R and CH2 from `raw_stream.csv` (e.g. 8 s windows):**
  - At the rotation line (16.7 Hz and harmonics), get the CH2/R gain and
    phase. That gives the DC-path low-pass corner.
  - Look for a tone near **54 Hz** in CH2: 7350 Hz aliased by 128 Hz
    sampling (7350 − 57×128 = 54). In burst mode at 512 Hz it lands at
    182 Hz (7350 − 14×512). If it's there,
    Aux In 3 sees unfiltered laser modulation, and an RC low-pass before
    Aux In 3 would help.
- **Block-length truncation:** at 1000 RPM one rotation is 60 ms (7.7
  samples at 128 Hz). The GUI's 1 s blocks hold 16.7 rotations, not a whole
  number. From `raw_stream.csv`, compare means over whole rotations with
  1 s block means. If the difference is above about 0.3% of R, average over
  whole rotations in software.
- **Aux In resolution:** take the smallest CH2 step from a histogram. S211
  showed 0.488 mV, against the SR830's nominal ~0.33 mV.

## What each outcome means for the software

- **H1 (CH2 roughly constant with SLVL):**
  - Lowering SLVL can't give laser-off bursts, so measuring thermal emission
    alone needs a shutter or the laser driver's enable input.
  - g has to be recalibrated whenever SLVL changes.
  - A cold-start calibration of a and g per run (CH2 − a − g·R_mean) is the fix.
- **H2 (CH2 ∝ SLVL):** SLVL at minimum turns the laser off. The software could
  interleave thermal-only bursts, for example one in ten at SLVL 0.004, to
  measure a and the thermal signal directly during growth. This is the
  strongest option.
- **H3 (electrical):** fix it in hardware (shielding or filtering on Aux In 3).
  Leak subtraction against R would only be approximate.
- **g depends on RPM:** use burst means over whole rotations.
- **The modulation alias (54 Hz when streaming) is present:** add an RC low-pass (corner a few Hz to
  ~20 Hz) before Aux In 3.

## Growth-run checklist (debug build, branch `debug-raw-capture`)

To get the most pyrometry information out of a normal growth:

1. **Start logging while cold**, laser aligned, before the heat-up. Leave
   at least 3 min. That calibrates a and g with no thermal term.
2. **Block the beam for about 30 s** at the start and log the event
   ("beam blocked" / "beam unblocked"). That gives the dark offset a directly.
3. **Keep SLVL at 1 Vrms for the whole run**, so one value of g applies throughout.
4. **Log every process event** in the event field: setpoint changes, thermocouple
   readings (at least at each plateau, and every ~10 min while at
   temperature), TMGa/NH3 on/off, rotation changes. These are the known
   temperatures the pyrometer calibration needs.
5. **Keep logging through the cool-down until cold.** That gives a second
   calibration of a and g. Block the beam again at the end.
6. Don't rely on the analysis panel's T readout; it's biased by the leak.
   The raw data is what matters, and it's all recorded.

## Still unknown (David)

- Is Aux In 3 the DC output of the same photodiode as the lock-in input, or
  a separate detector?
- Is there a 650 nm bandpass or laser-line filter in front of it? Without
  one, the effective pyrometer wavelength is more like 0.9–1.0 µm, and the
  650 nm Wien constant under-reads temperature excursions by about 30%.
- Is the thermocouple or setpoint logged anywhere we could align with the data?

## Code coordination

- Branch `debug-raw-capture` (commit after `4cd7613`, the TRCB commit) adds
  the `SRS830_DEBUG_RAW` raw-burst recorder (on) and an event log. It's tested
  against a scripted SR830, not yet against the real instrument. The debug
  queries are fail-safe, so they can't stop acquisition.
- Not merged into `main`. Please don't edit this branch's worktree.
