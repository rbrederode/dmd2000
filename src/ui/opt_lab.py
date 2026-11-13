"""LAB survey comparison for one cached OPT SKY aggregate."""
import csv
import json
import os
from pathlib import Path

import numpy as np
from matplotlib import pyplot as plt

from util.lab_profiler import LABGaussianExtractor, compare_spectra, match_channels, range_values
from obs.obs_display import velocity_limits, save_velocity_figure
from obs.spectral_annotations import annotate_spectral_metrics


def render_lab_comparison(records, selection, settings, out, result, progress=None):
    record = next((row for row in records if row["id"] == selection["aggregate_id"]), None)
    if record is None:
        raise ValueError("The selected aggregate has no usable cached velocity spectrum")
    path = Path(os.environ.get("DMD_LAB_FITS", "~/github/lab-fit/lab.fit")).expanduser()
    if not path.is_file():
        raise ValueError("LAB survey cube is missing; configure DMD_LAB_FITS on the server")
    direction = record["direction"].galactic
    longitude, latitude = float(direction.l.deg), float(direction.b.deg)
    beam = selection["beam"]
    nominal_longitude, nominal_latitude = longitude, latitude
    v = np.asarray(record["velocity"])
    power = np.ma.asarray(record["power"], dtype=float).filled(np.nan)
    valid = np.isfinite(v) & np.isfinite(power)
    lo, hi = velocity_limits(v, settings["baseline_vmin"], settings["baseline_vmax"])
    sweep = selection.get("sweep")
    longitude_offsets = range_values(",".join(map(str, sweep["longitude"]))) if sweep else np.array([0.])
    latitude_offsets = range_values(",".join(map(str, sweep["latitude"]))) if sweep else np.array([0.])
    total, candidates, best = len(longitude_offsets) * len(latitude_offsets), [], None
    with LABGaussianExtractor(path) as extractor:
        for dl in longitude_offsets:
            for db in latitude_offsets:
                longitude, latitude = (nominal_longitude + float(dl)) % 360, nominal_latitude + float(db)
                if progress and (len(candidates) % 10 == 0 or len(candidates) == total - 1):
                    progress(f"LAB candidate {len(candidates) + 1}/{total}", .94 + .035 * len(candidates) / total)
                rms, corr, status = np.inf, np.nan, "outside_latitude"
                if -90 <= latitude <= 90:
                    try:
                        velocity, temperature = extractor.extract(longitude, latitude, beam)
                        velocity, temperature = match_channels(velocity, temperature)
                        finite = np.isfinite(velocity) & np.isfinite(temperature)
                        velocity, temperature = velocity[finite], temperature[finite]
                        order = np.argsort(velocity)
                        velocity, temperature = velocity[order], temperature[order]
                        if len(velocity) < 2:
                            raise ValueError("No valid LAB coverage")
                        overlap = valid & (v >= lo) & (v <= hi) & (v >= velocity[0]) & (v <= velocity[-1])
                        visible = (velocity >= lo) & (velocity <= hi)
                        if overlap.sum() < 10 or not np.any(visible):
                            raise ValueError("Insufficient common velocity coverage")
                        observed_peak, lab_peak = np.max(power[overlap]), np.max(temperature[visible])
                        if observed_peak <= 0 or lab_peak <= 0:
                            raise ValueError("Comparison requires positive observation and survey peaks")
                        scale = float(lab_peak / observed_peak)
                        rms, corr = compare_spectra(v[valid], power[valid], velocity, temperature, lo, hi)
                        status = "ok" if np.isfinite(rms) else "unscorable"
                        # A nominal comparison can still be displayed without a shape score.
                        if (not sweep or np.isfinite(rms)) and (best is None or rms < best[0]):
                            best = (rms, corr, longitude, latitude, float(dl), float(db), velocity.copy(), temperature.copy(), scale)
                    except ValueError as exc:
                        status = str(exc)
                candidates.append([longitude, latitude, float(dl), float(db),
                                   float(rms) if np.isfinite(rms) else "", float(corr) if np.isfinite(corr) else "", status])
    if sweep:
        with (out / (record["id"] + "-lab-sweep.csv")).open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["longitude_deg", "latitude_deg", "delta_longitude_deg", "delta_latitude_deg", "shape_rms", "correlation", "status"])
            writer.writerows(candidates)
    if best is None:
        raise ValueError("No valid LAB comparison found; check sweep coordinates, velocity coverage and signal")
    rms, corr, longitude, latitude, dl, db, velocity, temperature, scale = best
    boundary = bool(sweep and (
        (len(longitude_offsets) > 1 and dl in (longitude_offsets[0], longitude_offsets[-1])) or
        (len(latitude_offsets) > 1 and db in (latitude_offsets[0], latitude_offsets[-1]))))
    score = f"Shape RMS={rms:.5f}, correlation={corr:.5f}" if np.isfinite(rms) and np.isfinite(corr) else "Shape fit unavailable"
    if sweep:
        score = f"Offsets: Δl={dl:+.2f}°, Δb={db:+.2f}° | {score}"
        if boundary:
            score += "\nBest fit at search boundary; consider widening the sweep"
            warning = "LAB best fit is at the search boundary; consider widening the sweep"
            if warning not in result.setdefault("warnings", []):
                result["warnings"].append(warning)
    filename = record["id"] + "-lab.png"
    fig, axis = plt.subplots(figsize=(14, 6))
    try:
        axis.plot(velocity, temperature, "k--", label="LAB survey")
        axis.plot(v, power * scale, color="red", linewidth=1,
                  label=f"Observation (baseline: {settings['baseline']}; peak-scaled to LAB)")
        axis.set(xlim=(lo, hi), xlabel="LSRK velocity [km/s]",
                 ylabel="LAB brightness temperature / scaled observation [K]",
                 title=(f"Observation: {result['observation']}\n"
                        f"Target {record['target_id']}, frequency {record['freq_scan']} | "
                        f"{'Best fit LAB' if sweep else 'LAB'}: l={longitude:.3f}°, b={latitude:.3f}° | Beam FWHM={beam:g}°\n{score}"))
        axis.grid(alpha=.25)
        noise_metrics = annotate_spectral_metrics(axis, v, power*scale, lo, hi,
                                                  label="Observation (peak-scaled to LAB)",
                                                  flags=record.get("channel_flags"), amplitude_unit="scaled plot units",
                                                  qa_settings=record.get("qa_settings"))
        save_velocity_figure(fig, axis, out / filename)
        if record.get("terminal_velocity"):
            from obs.terminal_velocity import annotate_terminal_velocity
            annotate_terminal_velocity(axis, record["terminal_velocity"], amplitude_scale=scale)
            save_velocity_figure(fig, axis, out / (record["id"] + "-lab-terminal.png"))
    finally:
        plt.close(fig)
    metadata = dict(aggregate_id=record["id"], longitude=longitude, latitude=latitude, beam_fwhm_deg=beam,
                    survey="LAB", cube=str(path), baseline=settings["baseline"],
                    velocity_range_kms=[lo, hi], observation_peak_scale=scale,
                    shape_rms=float(rms) if np.isfinite(rms) else None,
                    correlation=float(corr) if np.isfinite(corr) else None,
                    pointing_sweep=bool(sweep), nominal_longitude=nominal_longitude, nominal_latitude=nominal_latitude,
                    delta_longitude_deg=dl, delta_latitude_deg=db, sweep_ranges=sweep,
                    candidates_evaluated=total, boundary_solution=boundary,
                    ranking="minimum normalized shape RMS within selected velocity limits")
    metadata["observation_noise"] = noise_metrics
    if record.get("terminal_velocity"):
        metadata["observation_terminal_velocity"] = record["terminal_velocity"]
    (out / (record["id"] + "-lab.json")).write_text(json.dumps(metadata, indent=2, allow_nan=False) + "\n")
    with (out / (record["id"] + "-lab.csv")).open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["velocity_km_s", "lab_brightness_K"])
        writer.writerows(zip(velocity, temperature))
    row = next(row for row in result["aggregates"] if row["id"] == record["id"])
    row["views"]["lab"] = filename
    row["lab_comparison"] = metadata
    if record.get("terminal_velocity"):
        row["views"]["lab_terminal"] = record["id"] + "-lab-terminal.png"
    result["lab_comparison"] = metadata
