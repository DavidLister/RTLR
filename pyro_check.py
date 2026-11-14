# pyro_check.py
#
# Short-term pyrometer readout from the CH2 (Aux In 3) channel, for checking
# that the wafer reaches the same temperature from run to run (e.g. the bake).
#
# Why this exists: the 650 nm bandpass in front of the photodiode does not
# block the near-IR, so the thermal part of CH2 is dominated by ~1 um light
# (S432 fit: lambda_eff = 1.21 um). Constants are shared with the live
# readout (pyrometer.py, common.PYRO_*). The 650 nm Wien + (1 - R) emissivity
# correction in analysis.py therefore does not apply. Until a shortpass filter
# is added, this treats CH2 as an empirically calibrated ~1.2 um pyrometer:
#
#     S = CH2 - a - g * R          (a: dark offset, g: laser DC leak per unit R)
#     1/T = 1/T_ref - (lambda_eff / c2) * ln(S / S_ref)
#
# a and g are measured in each run (laser-off periods and the cold, laser-on
# start). The reference (S_ref, T_ref) is fixed from S432 (2026-11-13, HT
# deposition at TC = 1000.9 C), so every run is reported on the same scale and
# bake plateaus can be compared between runs. No emissivity correction: at
# ~1 um the sapphire is nearly transparent and the reading is dominated by the
# susceptor under the wafer.
#
# Caveats: a change in optical throughput (alignment, window coating) of 1%
# shifts T by ~1.4 K at 1020 C. Compare the cold R levels printed for each run
# to spot this. Below ~700 C the leak uncertainty dominates.
#
# Usage:
#   python pyro_check.py <run_dir> [--datalog <reactor.csv>] [--offset S]
#                        [--cold START END] [--plot out.png]
#
# --offset is reactor clock minus RTLR clock in seconds (147 for S432). If a
# datalog is given without --offset, the offset is estimated by matching the
# pyrometer to the thermocouple (AI56); that estimate includes the ~20 s
# thermocouple lag.

import argparse
import glob
import json
import os

import numpy as np
import pandas as pd

import common
import pyrometer

C2 = pyrometer.C2_M_K
LAMBDA_EFF = common.PYRO_LAMBDA_EFF_UM * 1e-6
S_REF = common.PYRO_REF_SIGNAL_V
T_REF_C = common.PYRO_REF_TEMP_C
TC_LAG_S = 20.0           # thermocouple lags the pyrometer by ~20 s (S432)
LASER_ON_MIN_R = common.PYRO_LASER_OFF_R_V


def load_run(run_dir):
    """1 s series of t, R, CH2 (sign-corrected). Prefers raw_stream.csv."""
    stream = os.path.join(run_dir, "raw_stream.csv")
    if os.path.exists(stream):
        s = pd.read_csv(stream)
        s.columns = ["t", "R", "C"]
        origin = None
        inst = os.path.join(run_dir, "raw", "instrument.json")
        if os.path.exists(inst):
            origin = json.load(open(inst)).get("time_origin_epoch")
        s["t"] -= origin if origin else s.t.iloc[0]
        sec = np.floor(s.t).astype(int)
        b = s.groupby(sec).agg(R=("R", "mean"), C=("C", "mean")).reset_index()
        b.columns = ["t", "R", "C"]
        b["t"] = b.t + 0.5
        start_local = None
        if os.path.exists(inst):
            start_local = pd.Timestamp(json.load(open(inst))["connected"]) \
                if origin is None else pd.Timestamp.fromtimestamp(origin)
        return b, "raw_stream (1 s means)", start_local
    files = sorted(glob.glob(os.path.join(run_dir, "*Reflectance.csv")))
    if not files:
        raise SystemExit(f"No raw_stream.csv or *Reflectance.csv in {run_dir}")
    r = pd.read_csv(files[0], encoding="utf-8-sig")
    if r.shape[1] < 3:
        raise SystemExit("Reflectance file has no CH2 column")
    r = r.iloc[:, :3]
    r.columns = ["t", "R", "C"]
    return r, os.path.basename(files[0]) + " (upper-median R)", None


def estimate_leak(b, cold):
    """Dark offset a from laser-off periods; g from the cold laser-on window."""
    off = b.R < LASER_ON_MIN_R
    a = float(b.C[off].median()) if off.sum() >= 5 else 0.0
    on = ~off
    if cold is None:
        # first 4 min of laser-on data, stopping early if CH2 starts rising
        t_on = b.t[on]
        if t_on.empty:
            raise SystemExit("Laser never on in this run")
        w = on & (b.t >= t_on.iloc[0] + 5) & (b.t <= t_on.iloc[0] + 245)
        c0 = b.C[w].iloc[:30].median()
        w &= b.C < c0 * 1.03
        cold = (float(b.t[w].min()), float(b.t[w].max()))
    w = on & (b.t >= cold[0]) & (b.t <= cold[1])
    g = float(np.median((b.C[w] - a) / b.R[w]))
    return a, g, cold, int(off.sum()), float(b.R[w].mean())


def pyro_temperature(S):
    return np.array([pyrometer.wien_temperature_c(s, S_REF, T_REF_C) for s in np.atleast_1d(S)])


def load_datalog(path):
    d = pd.read_csv(path, skiprows=4, skipinitialspace=True, index_col=False)
    d = d.loc[:, ~d.columns.str.startswith("Unnamed")]
    d["ts"] = pd.to_datetime(d["Date"] + " " + d["Time"], format="%m/%d/%Y %I:%M:%S %p")
    return d


def find_plateaus(t, T, min_len=120, tol=2.0, min_T=700.0):
    """Stretches where T stays within +-tol of its running median for >= min_len s."""
    out, i, n = [], 0, len(T)
    while i < n:
        if not np.isfinite(T[i]) or T[i] < min_T:
            i += 1
            continue
        j = i
        while j + 1 < n and np.isfinite(T[j + 1]) and abs(T[j + 1] - np.median(T[i:j + 2])) < tol:
            j += 1
        if t[j] - t[i] >= min_len:
            out.append((i, j))
            i = j + 1
        else:
            i += 1
    return out


def main():
    ap = argparse.ArgumentParser(description="Pyrometer readout from CH2 on a fixed (S432) scale")
    ap.add_argument("run_dir")
    ap.add_argument("--datalog")
    ap.add_argument("--offset", type=float, help="reactor clock minus RTLR clock (s)")
    ap.add_argument("--cold", type=float, nargs=2, metavar=("START", "END"),
                    help="cold laser-on window (s) for the leak ratio g")
    ap.add_argument("--plot")
    ap.add_argument("--bake-min", type=float, default=1005.0,
                    help="first plateau at or above this (pyrometer, C) is taken as the bake")
    ap.add_argument("--bake-nominal", type=float, default=1020.0,
                    help="bake temperature to pin to when there is no datalog (C)")
    args = ap.parse_args()

    b, source, start_local = load_run(args.run_dir)
    a, g, cold, n_off, R_cold = estimate_leak(b, args.cold)
    on = b.R >= LASER_ON_MIN_R
    b["S"] = b.C - a - g * b.R
    b["T"] = np.where(on & (b.S > 0.02), pyro_temperature(b.S.values), np.nan)

    print(f"Run: {args.run_dir}\nData: {source}")
    print(f"Dark offset a = {1e3 * a:.2f} mV ({n_off} laser-off s"
          f"{'' if n_off >= 5 else ', none found: a = 0 assumed'})")
    print(f"Leak g = {g:.2f} from cold window {cold[0]:.0f}-{cold[1]:.0f} s; "
          f"cold R = {1e3 * R_cold:.2f} mV (compare between runs: throughput check)")
    print(f"Scale: lambda_eff {LAMBDA_EFF * 1e6:.2f} um, ref S {S_REF:.3f} V = {T_REF_C} C (S432)")

    tc = None
    if args.datalog:
        if start_local is None:
            raise SystemExit("Datalog alignment needs raw/instrument.json in the run")
        d = load_datalog(args.datalog)
        t_dl = (d.ts - start_local).dt.total_seconds().values
        Ts = pd.Series(d.AI56.values).rolling(9, center=True, min_periods=1).mean().values
        T_corr = Ts + TC_LAG_S * np.gradient(Ts, t_dl)
        if args.offset is None:
            best = None
            for off in np.arange(-900, 901, 1.0):
                tc_try = np.interp(b.t, t_dl - off, T_corr, left=np.nan, right=np.nan)
                m = np.isfinite(b["T"]) & (tc_try > 700)
                if m.sum() < 120:
                    continue
                rms = np.sqrt(np.mean((b["T"][m] - tc_try[m]) ** 2))
                if best is None or rms < best[1]:
                    best = (off, rms)
            if best is None:
                raise SystemExit("Could not match pyrometer to thermocouple; pass --offset")
            args.offset = best[0]
            print(f"Clock offset (estimated) = {args.offset:.0f} s")
        tc = np.interp(b.t, t_dl - args.offset, T_corr, left=np.nan, right=np.nan)
        b["TC"] = tc
        m = np.isfinite(b["T"]) & (tc > 700)
        if m.any():
            dT = b["T"][m] - tc[m]
            print(f"Pyro - TC (TC lag-corrected, TC > 700 C): mean {dT.mean():+.1f} K, rms {dT.std():.1f} K")

    print("\nPlateaus (pyrometer steady within +-2 K for >= 2 min, above 700 C):")
    print("   start    end   len   T_pyro (C)   sd   drift (K/min)" + ("   TC (C)  pyro-TC" if tc is not None else ""))
    t, T = b.t.values, b["T"].values
    plateaus = find_plateaus(t, T)
    for i, j in plateaus:
        seg = slice(i, j + 1)
        drift = np.polyfit(t[seg], T[seg], 1)[0] * 60
        line = (f"  {t[i]:6.0f} {t[j]:6.0f} {t[j] - t[i]:5.0f}   {np.nanmean(T[seg]):8.1f}   "
                f"{np.nanstd(T[seg]):4.2f}   {drift:+6.2f}")
        if tc is not None:
            line += f"      {np.nanmean(tc[seg]):7.1f}  {np.nanmean(T[seg] - tc[seg]):+6.1f}"
        print(line)

    # Bake-referenced comparison. Alignment and window throughput scale S by a
    # constant factor within a run, which cancels in S_plateau / S_bake, so
    # these differences are comparable between runs even when the absolute
    # S432-scale readings are not.
    bake = next((p for p in plateaus if np.nanmean(T[p[0]:p[1] + 1]) >= args.bake_min), None)
    if bake is not None:
        S = b.S.values
        bs = slice(bake[0], bake[1] + 1)
        S_bake = np.nanmedian(S[bs])
        T_bake_tc = np.nanmean(tc[bs]) if tc is not None else args.bake_nominal
        label = "TC" if tc is not None else f"nominal {args.bake_nominal:.0f}"
        print(f"\nRelative to the bake (t {t[bake[0]]:.0f}-{t[bake[1]]:.0f} s, pinned to {label} = "
              f"{T_bake_tc:.1f} C; throughput cancels):")
        print("   start    end   dT_IR (K)" + ("   dT_TC (K)   IR-TC (K)" if tc is not None else "   T_IR (C)"))
        for i, j in plateaus:
            if (i, j) == bake:
                continue
            seg = slice(i, j + 1)
            T_ir = pyrometer.wien_temperature_c(np.nanmedian(S[seg]), S_bake, T_bake_tc)
            line = f"  {t[i]:6.0f} {t[j]:6.0f}   {T_ir - T_bake_tc:+8.1f}"
            if tc is not None:
                d_tc = np.nanmean(tc[seg]) - T_bake_tc
                line += f"    {d_tc:+8.1f}    {T_ir - T_bake_tc - d_tc:+7.1f}"
            else:
                line += f"    {T_ir:7.1f}"
            print(line)

    if args.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(11, 4.5))
        if tc is not None:
            ax.plot(b.t / 60, tc, color="#eb6834", lw=1.5, label="Thermocouple AI56 (lag-corrected)")
        ax.plot(b.t / 60, b["T"], color="#2a78d6", lw=1, label="Pyrometer (CH2, ~1.2 um, S432 scale)")
        ax.set_xlabel("Time since RTLR start (min)")
        ax.set_ylabel("Temperature (C)")
        ax.set_ylim(650, max(1050, np.nanmax(b["T"]) + 10))
        ax.spines[["top", "right"]].set_visible(False)
        ax.legend(frameon=False, fontsize=8)
        ax.set_title(os.path.basename(os.path.normpath(args.run_dir)), loc="left")
        fig.tight_layout()
        fig.savefig(args.plot, dpi=110)
        print(f"\nPlot: {args.plot}")


if __name__ == "__main__":
    main()
