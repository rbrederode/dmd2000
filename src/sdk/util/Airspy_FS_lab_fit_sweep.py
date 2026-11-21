#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import glob
import argparse
from datetime import datetime
from pathlib import Path
import sys

import numpy as np
import matplotlib.pyplot as plt

from astropy.time import Time
from astropy.coordinates import SkyCoord, EarthLocation
from astropy.units import deg, m


# ============================================================
# Paths
# ============================================================

SCRIPT_DIR = Path(
    "/Users/s.asayama/Documents/Work/Intern/DIY_Parabola/Data_reduction/AzEl_fitting/"
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from sdk.util.lab_profiler import LABGaussianExtractor, match_channels
LAB_FIT = SCRIPT_DIR / "lab.fit"


# ============================================================
# Arguments
# ============================================================

def parse_range(s):
    values = s.strip("[]").split(",")

    if len(values) != 3:
        raise ValueError(f"Invalid range: {s}")

    vmin, vmax, step = map(float, values)

    if step <= 0 or vmax < vmin:
        raise ValueError(f"Invalid range: {s}")

    return vmin, vmax, step


parser = argparse.ArgumentParser(
    description="AZ/EL sweep and LAB spectrum fitting"
)

parser.add_argument(
    "-f", "--file",
    required=True,
    help="Input observation CSV file"
)

parser.add_argument(
    "--az",
    default="[0,360,2]",
    help="AZ range [min,max,step]"
)

parser.add_argument(
    "--el",
    default="[85,90,1]",
    help="EL range [min,max,step]"
)

args = parser.parse_args()

filename = args.file

az_min, az_max, az_step = parse_range(args.az)
el_min, el_max, el_step = parse_range(args.el)

az_list = np.round(
    np.arange(az_min, az_max + az_step / 2, az_step),
    10
)

el_list = np.round(
    np.arange(el_min, el_max + el_step / 2, el_step),
    10
)

print("\n===================================================")
print("AZ / EL sweep")
print("===================================================")
print(f"AZ = {az_min} ... {az_max}  step={az_step}")
print(f"EL = {el_min} ... {el_max}  step={el_step}")
print(f"Total points = {len(az_list) * len(el_list)}")
print("===================================================")


# ============================================================
# Observatory / Observation
# ============================================================

obs_loc = EarthLocation(
    lon=-2.308576498655725 * deg,
    lat=53.236539429110486 * deg,
    height=77 * m
)

obs_list = sorted(
    glob.glob("UT*ASmini_vlsr*.csv")
)

timestamp = filename.split("_")[0]

ut = Time(
    datetime.strptime(timestamp, "UT%Y%m%dT%H%M%S"),
    scale="utc"
)

idx = obs_list.index(filename)

HI = np.load("HI_bl.npy")[idx]
v = np.load("Vlsr_bl.npy")[idx]


# ============================================================
# Functions
# ============================================================

def get_lb(az, el):
    """Convert AZ/EL to Galactic coordinates."""

    sky = SkyCoord(
        az,
        el,
        frame="altaz",
        unit="deg",
        obstime=ut,
        location=obs_loc
    )

    gal = sky.galactic

    return gal.l.deg, gal.b.deg


def get_lab(l, b):
    """Extract and pair-average LAB channels through the consolidated profiler."""
    with LABGaussianExtractor(LAB_FIT) as extractor:
        return match_channels(*extractor.extract(l, b, 15.0))


def compare_spectra(Vref, Pref):
    """
    Compare normalized DIY and LAB spectra.

    Comparison range:
        -150 <= Vlsr <= +150 km/s

    Both spectra are independently normalized to their
    maximum within this range. Points below 0.01 are masked.

    RMS is calculated from the normalized difference:
        RMS = sqrt(mean((DIY - LAB)^2))
    """

    order = np.argsort(Vref)
    Vref = Vref[order]
    Pref = Pref[order]

    valid = (
        (v >= Vref.min()) &
        (v <= Vref.max()) &
        np.isfinite(HI)
    )

    if valid.sum() < 10:
        return np.inf, np.nan, None, None, None, None

    v_fit = v[valid]

    lab = np.interp(
        v_fit,
        Vref,
        Pref
    )

    diy = HI[valid]

    finite = (
        np.isfinite(diy) &
        np.isfinite(lab)
    )

    v_fit = v_fit[finite]
    diy = diy[finite]
    lab = lab[finite]

    velocity_mask = (
        (v_fit >= -150) &
        (v_fit <= 150)
    )

    if velocity_mask.sum() < 10:
        return np.inf, np.nan, None, None, None, None

    v_fit = v_fit[velocity_mask]
    diy = diy[velocity_mask]
    lab = lab[velocity_mask]

    diy_max = np.max(diy)
    lab_max = np.max(lab)

    if (
        not np.isfinite(diy_max) or
        not np.isfinite(lab_max) or
        diy_max <= 0 or
        lab_max <= 0
    ):
        return np.inf, np.nan, None, None, None, None

    diy_norm = diy / diy_max
    lab_norm = lab / lab_max

    mask = (
        np.isfinite(diy_norm) &
        np.isfinite(lab_norm) &
        (diy_norm > 0.01) &
        (lab_norm > 0.01)
    )

    if mask.sum() < 10:
        return np.inf, np.nan, None, None, None, None

    diy_fit = diy_norm[mask]
    lab_fit = lab_norm[mask]

    rms = np.sqrt(
        np.mean(
            (diy_fit - lab_fit) ** 2
        )
    )

    corr = np.corrcoef(
        diy_fit,
        lab_fit
    )[0, 1]

    return (
        rms,
        corr,
        v_fit,
        diy_norm,
        lab_norm,
        mask
    )


# ============================================================
# AZ / EL Sweep
# ============================================================

results = []

for az in az_list:
    for el in el_list:

        print(
            f"AZ={az:6.1f} "
            f"EL={el:6.1f}",
            end=" "
        )

        l, b = get_lb(az, el)
        Vref, Pref = get_lab(l, b)

        (
            rms,
            corr,
            v_fit,
            diy_norm,
            lab_norm,
            mask
        ) = compare_spectra(Vref, Pref)

        print(
            f"l={l:8.3f} "
            f"b={b:7.3f} "
            f"RMS={rms:.6f} "
            f"corr={corr:.6f}"
        )

        results.append({
            "az": az,
            "el": el,
            "l": l,
            "b": b,
            "rms": rms,
            "corr": corr,
            "Vref": Vref,
            "Pref": Pref,
            "v_fit": v_fit,
            "diy_norm": diy_norm,
            "lab_norm": lab_norm,
            "mask": mask
        })


# ============================================================
# Best result
# ============================================================

valid_results = [
    r for r in results
    if np.isfinite(r["rms"])
]

if not valid_results:
    raise RuntimeError(
        "No valid spectrum comparison was found."
    )

best = min(
    valid_results,
    key=lambda r: r["rms"]
)

print("\n===================================================")
print("BEST MATCH")
print("===================================================")
print(
    f"AZ = {best['az']:.2f} deg\n"
    f"EL = {best['el']:.2f} deg\n"
    f"l  = {best['l']:.4f} deg\n"
    f"b  = {best['b']:.4f} deg\n"
    f"RMS = {best['rms']:.6f}\n"
    f"Correlation = {best['corr']:.6f}"
)
print("===================================================")


# ============================================================
# Plot
# ============================================================

plt.figure(figsize=(10, 6))

plt.plot(
    best["v_fit"],
    best["diy_norm"],
    "r",
    linewidth=1.5,
    label="DIY telescope"
)

plt.plot(
    best["v_fit"],
    best["lab_norm"],
    "--k",
    linewidth=1.5,
    label="LAB survey data"
)

plt.xlim(-200, 200)
plt.ylim(-0.05, 1.05)

plt.xlabel("Radial Velocity [km/s]")
plt.ylabel("Normalized Brightness Temperature")

plt.title(
    f"Best match: "
    f"AZ={best['az']:.2f}°, "
    f"EL={best['el']:.2f}°\n"
    f"(l={best['l']:.2f}°, "
    f"b={best['b']:.2f}°, "
    f"RMS={best['rms']:.3f}, "
    f"corr={best['corr']:.3f})"
)

plt.legend()
plt.grid(alpha=0.2)
plt.tight_layout()

plt.savefig(
    filename[:-4] + "_best_fit.png",
    dpi=150
)

plt.show()
