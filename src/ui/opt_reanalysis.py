"""Re-render derived OPT views from cached native LSRK spectra, without disk replay."""
import csv
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import numpy as np
from matplotlib import pyplot as plt
from astropy.coordinates import SkyCoord
from astropy import units as u

from obs.obs_display import ObsDisplay, SCAN_COLOURS
from util.convert import subtract_spectrum_baseline


def save_velocity(records, path, observation, baseline):
    fig, axis = plt.subplots(figsize=(14, 6))
    for index, record in enumerate(records):
        axis.plot(record["velocity"], record["power"], color=SCAN_COLOURS[index % len(SCAN_COLOURS)],
                  label=f"Target {record['target_id']}, frequency {record['freq_scan']} ({record['center_freq'] / 1e6:.3f} MHz)")
    axis.axvline(0, color="black", linestyle="--", linewidth=1, label="Zero LSRK velocity")
    axis.axhline(0, color="grey", linestyle=":", linewidth=1)
    axis.set(title=f"Observation {observation} — Aggregated Sky Scans (baseline: {baseline})",
             xlabel="LSRK velocity [km/s]", ylabel="Calibrated power [a.u.]" if baseline == "none" else "Baseline-subtracted calibrated power [a.u.]")
    axis.grid(alpha=.25)
    axis.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def reanalyse(request, out, progress):
    source = Path(request["source_results"])
    result = json.loads((source / "result.json").read_text())
    settings = request["settings"]
    # Keep completed signal displays and invariant observation products.
    for path in source.iterdir():
        if path.name in ("native-spectra.npz", "native-spectra.json", "timeline.png", "overview.png") or path.name.endswith("-signal.png") or path.name.endswith("-sky-apr.csv"):
            shutil.copy2(path, out / path.name)
    result["settings"] = settings
    result["views"] = {key: name for key, name in result["views"].items() if key not in ("velocity", "lv")}
    result["warnings"] = []
    result["analysis_only"] = True
    labels = json.loads((source / "native-spectra.json").read_text())
    targets = {}
    records = []
    with np.load(source / "native-spectra.npz", allow_pickle=False) as arrays:
        for index, label in enumerate(labels):
            progress(f"Updating derived spectrum {index + 1}/{len(labels)}", .1 + .7 * index / max(1, len(labels)))
            row = next(item for item in result["aggregates"] if item["id"] == label["id"])
            row["views"].pop("velocity", None)
            velocity = arrays[f"{index}_velocity"]
            original = arrays[f"{index}_power"]
            mask = np.isfinite(original) & (velocity >= settings["baseline_vmin"]) & (velocity <= settings["baseline_vmax"])
            try:
                corrected, baseline = subtract_spectrum_baseline(velocity, original, settings["baseline"], mask)
            except ValueError as exc:
                result["warnings"].append(f"Target {label['target_id']}, frequency {label['freq_scan']}: {exc}")
                continue
            direction = SkyCoord(l=label["longitude"] * u.deg, b=label["latitude"] * u.deg, frame="galactic")
            targets[label["target_id"]] = SimpleNamespace(sky_coord=direction)
            record = {"target_id": label["target_id"], "freq_scan": label["freq_scan"], "direction": direction,
                      "center_freq": label["center_freq"], "velocity": velocity, "raw_power": np.ma.masked_invalid(original),
                      "power": np.ma.masked_invalid(corrected), "baseline": baseline}
            records.append(record)
            filename = f"{label['id']}-velocity.png"
            save_velocity([record], out / filename, result["observation"], settings["baseline"])
            row["views"]["velocity"] = filename
    if records:
        save_velocity(records, out / "velocity.png", result["observation"], settings["baseline"])
        result["views"]["velocity"] = "velocity.png"
        with (out / "velocity.csv").open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["target_id", "freq_scan", "velocity_km_s", "calibrated_power", "baseline", "baseline_subtracted_power"])
            for record in records:
                for i, velocity in enumerate(record["velocity"]):
                    original = record["raw_power"][i]
                    values = ["", "", ""] if np.ma.is_masked(original) else [float(original), float(record["baseline"][i]), float(record["power"][i])]
                    writer.writerow([record["target_id"], record["freq_scan"], float(velocity), *values])
        progress("Updating longitude–velocity diagram", .85)
        display = ObsDisplay(result["observation"])
        display.lsr_spectra = records
        display.lsr_baseline_method = settings["baseline"]
        obs = SimpleNamespace(get_target_by_index=lambda index: targets.get(index))
        fig = display.display_lv(obs, str(out / "lv.png"), settings["contours"], settings["contour_smoothing"])
        if fig is not None:
            result["views"]["lv"] = "lv.png"
            plt.close(fig)
    else:
        result["warnings"].append("No velocity spectra could be baseline-fitted with these settings. Signal displays are retained.")
    return result
