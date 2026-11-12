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

from obs.obs_display import ObsDisplay, SCAN_COLOURS, save_velocity_figure, save_frequency_record, save_frequency_overlay, velocity_limits, plot_channel_flags
from util.convert import select_baseline_windows
from sdp.spectral_baseline import subtract_baseline as subtract_spectrum_baseline
from obs.spectral_annotations import annotate_spectral_metrics
from ui.opt_pipeline import qa_metric_settings
from obs.total_power import load_power_cache, render_power_products, mark_power_unavailable


def save_velocity(records, path, observation, baseline, vmin, vmax, *, show_metrics=True):
    fig, axis = plt.subplots(figsize=(14, 6))
    for index, record in enumerate(records):
        axis.plot(record["velocity"], record["power"], color=SCAN_COLOURS[index % len(SCAN_COLOURS)],
                  label=f"Tgt {record['target_id']}, Freq {record['freq_scan']}")
        plot_channel_flags(axis, record["velocity"], record["power"], record.get("channel_flags"))
        if show_metrics:
            low, high = velocity_limits(record["velocity"], vmin, vmax)
            annotate_spectral_metrics(axis, record["velocity"], record["power"], low, high,
                                      label=f"Tgt {record['target_id']}, Freq {record['freq_scan']}",
                                  flags=record.get("channel_flags"), qa_settings=record.get("qa_settings"))
    axis.axvline(0, color="black", linestyle="--", linewidth=1, label="Zero LSRK velocity")
    axis.set(title=f"Observation {observation} — Aggregated Sky Scans (baseline: {baseline})",
             xlabel="LSRK velocity [km/s]", ylabel="Calibrated power [a.u.]" if baseline == "none" else "Baseline-subtracted calibrated power [a.u.]")
    axis.set_xlim(*velocity_limits(np.concatenate([record["velocity"] for record in records]), vmin, vmax))
    axis.grid(alpha=.25)
    save_velocity_figure(fig, axis, path)
    plt.close(fig)


def reanalyse(request, out, progress):
    source = Path(request["source_results"])
    result = json.loads((source / "result.json").read_text())
    settings = request["settings"]
    has_power_cache = (source / "native-total-power.npz").is_file()
    keep_legacy_power = not has_power_cache and settings["baseline"] == "none" and result.get("settings", {}).get("baseline", "none") == "none"
    # Keep invariant observation products; Frequency images are regenerated below.
    for path in source.iterdir():
        power_product = path.name in ("timeline.png", "timeline-seconds.png", "total-power-seconds.csv", "total-power-scans.csv") or path.name.endswith(("-timeline.png", "-timeline-seconds.png"))
        invariant = path.name in ("native-spectra.npz", "native-spectra.json", "native-total-power.npz", "native-total-power.json", "pipeline-config.json", "overview.png") or path.name.endswith(("-signal.png", "-frequency.png", "-sky-apr.csv"))
        if invariant or (power_product and keep_legacy_power):
            shutil.copy2(path, out / path.name)
    result["settings"] = settings
    result["views"] = {key: name for key, name in result["views"].items() if key not in ("velocity", "lv", "frequency")}
    result["warnings"] = []
    result["analysis_only"] = True
    result.pop("lab_comparison", None)
    for row in result["aggregates"]:
        row["views"].pop("lab", None)
    labels = json.loads((source / "native-spectra.json").read_text())
    targets = {}
    records = []
    frequency_records = []
    velocity_available = False
    with np.load(source / "native-spectra.npz", allow_pickle=False) as arrays:
        for index, label in enumerate(labels):
            progress(f"Updating derived spectrum {index + 1}/{len(labels)}", .1 + .7 * index / max(1, len(labels)))
            row = next(item for item in result["aggregates"] if item["id"] == label["id"])
            policy = label.get("qa_settings") or qa_metric_settings(result.get("pipeline_config"), row.get("digitiser", "default"))
            row["views"].pop("velocity", None)
            frequency_key = f"{index}_frequency_mhz"
            if frequency_key in arrays or settings["baseline"] != "none":
                row["views"].pop("frequency", None)
            velocity = arrays[f"{index}_velocity"] if f"{index}_velocity" in arrays else None
            original = arrays[f"{index}_power"]
            channel_flags = arrays[f"{index}_channel_flags"] if f"{index}_channel_flags" in arrays else None
            if channel_flags is None:
                warning = "Reprocess this observation to show bandpass and RFI annotations from cached channel flags."
                if warning not in result["warnings"]:
                    result["warnings"].append(warning)
            if frequency_key in arrays:
                frequency_filename = f"{label['id']}-frequency.png"
                (out / frequency_filename).unlink(missing_ok=True)
                try:
                    corrected_frequency = save_frequency_record({"target_id": label["target_id"], "freq_scan": label["freq_scan"],
                                           "digitiser": row.get("digitiser"), "raw_power": original, "power": original, "channel_flags": channel_flags,
                                           "frequency_mhz": arrays[frequency_key], "velocity": velocity,
                                           "qa_settings": policy}, out / frequency_filename,
                                          result["observation"], settings["baseline"],
                                          fmin=settings.get("frequency_min_mhz"), fmax=settings.get("frequency_max_mhz"))
                    row["views"]["frequency"] = frequency_filename
                    frequency_records.append(corrected_frequency)
                    row.get("view_errors", {}).pop("frequency", None)
                except ValueError as exc:
                    row.setdefault("view_errors", {})["frequency"] = str(exc)
                    result["warnings"].append(f"Frequency view unavailable for target {label['target_id']}: {exc}")
            elif settings["baseline"] != "none":
                warning = "Reprocess this observation to cache frequency channels for Frequency baseline updates."
                if warning not in result["warnings"]:
                    result["warnings"].append(warning)
            if velocity is None:
                row.setdefault("view_errors", {})["velocity"] = label.get("velocity_error") or "No LSRK velocity coordinates were recorded. Frequency and Total Power remain available."
                continue
            velocity_available = True
            try:
                low, high = velocity_limits(velocity, settings["baseline_vmin"], settings["baseline_vmax"])
                mask = np.isfinite(original) & (velocity >= low) & (velocity <= high)
                windows = None
                if settings["baseline"] != "none":
                    windows = select_baseline_windows(velocity, original, low, high)
                    mask = np.zeros(len(velocity), dtype=bool)
                    for low, high in windows:
                        mask |= (velocity >= low) & (velocity <= high)
                corrected, baseline = subtract_spectrum_baseline(velocity, original, settings["baseline"], mask,
                                                                 fit_line_free=bool(windows))
            except ValueError as exc:
                result["warnings"].append(f"Target {label['target_id']}, frequency {label['freq_scan']}: {exc}")
                continue
            direction = SkyCoord(l=label["longitude"] * u.deg, b=label["latitude"] * u.deg, frame="galactic")
            targets[label["target_id"]] = SimpleNamespace(sky_coord=direction)
            record = {"target_id": label["target_id"], "freq_scan": label["freq_scan"], "direction": direction,
                      "center_freq": label["center_freq"], "velocity": velocity, "raw_power": np.ma.masked_invalid(original),
                      "power": np.ma.masked_invalid(corrected), "channel_flags": channel_flags,
                      "qa_settings": policy,
                      "baseline": baseline, "baseline_windows": windows, "id": label["id"],
                      "digitiser": row.get("digitiser"), "frequency_correction": baseline,
                      "frequency_mhz": arrays[frequency_key] if frequency_key in arrays else None}
            records.append(record)
            filename = f"{label['id']}-velocity.png"
            save_velocity([record], out / filename, result["observation"], settings["baseline"],
                          settings["baseline_vmin"], settings["baseline_vmax"])
            row["views"]["velocity"] = filename
            row.get("view_errors", {}).pop("velocity", None)
    if has_power_cache:
        power_basis = settings.get("total_power_basis", "frequency")
        render_power_products(load_power_cache(source), records if power_basis == "velocity" else frequency_records,
                              result, out, settings["baseline"], basis=power_basis)
    elif not keep_legacy_power:
        mark_power_unavailable(result, "Reprocess this observation once to cache flag-aware Total Power corrections; subsequent plot updates refresh both resolutions.")
    if frequency_records:
        save_frequency_overlay(frequency_records, out / "frequency.png", result["observation"], settings["baseline"])
        result["views"]["frequency"] = "frequency.png"
        result.get("view_errors", {}).pop("frequency", None)
    else:
        result.setdefault("view_errors", {})["frequency"] = "No updated individual Frequency spectra are available. Adjust the Frequency limits/baseline or reprocess to refresh the native cache."
    if records:
        save_velocity(records, out / "velocity.png", result["observation"], settings["baseline"],
                          settings["baseline_vmin"], settings["baseline_vmax"], show_metrics=False)
        result["views"]["velocity"] = "velocity.png"
        result.get("view_errors", {}).pop("velocity", None)
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
        fig = display.display_lv(obs, str(out / "lv.png"), settings["contours"], settings["contour_smoothing"],
                                 vmin=settings["baseline_vmin"], vmax=settings["baseline_vmax"])
        if fig is not None:
            result["views"]["lv"] = "lv.png"
            result.get("view_errors", {}).pop("lv", None)
            plt.close(fig)
    else:
        if not velocity_available:
            reason = next((row.get("view_errors", {}).get("velocity") for row in result["aggregates"]
                           if row.get("view_errors", {}).get("velocity")), "No LSRK coordinates are cached.")
            result.setdefault("view_errors", {}).update(velocity=reason, lv=f"The L–V view requires velocity coordinates. {reason}")
        result["warnings"].append("No velocity spectra could be baseline-fitted with these settings. Signal displays are retained."
                                  if velocity_available else "No LSRK coordinates are cached for this observation. Frequency and Total Power views remain available.")
    if request.get("lab_comparison"):
        progress("Extracting LAB survey comparison", .94)
        try:
            from ui.opt_lab import render_lab_comparison
            render_lab_comparison(records, request["lab_comparison"], settings, out, result, progress=progress)
        except (ValueError, OSError, KeyError) as exc:
            result["warnings"].append(f"LAB comparison unavailable: {exc}")
    return result
