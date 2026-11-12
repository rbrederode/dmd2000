"""Headless, isolated OPT processing worker used by ui.opt_service."""
from __future__ import annotations

import json
import logging
from pathlib import Path
import sys
import traceback
import warnings
import zipfile

import matplotlib
matplotlib.use("Agg")
from matplotlib import pyplot as plt
import numpy as np

from models.scan import ScanState, ScanType
from obs.obs_display import ObsDisplay, save_frequency_record, save_frequency_overlay
from obs.opt import process_observation, save_aggregated_power_csv
from sdp.channel_mask import valid_channels, masked_values
from sdp.signal_display import SignalDisplay
from ui.opt_service import atomic_json
from ui.opt_pipeline import baseline_applied, qa_metric_settings
from obs.total_power import physical_power_records, save_power_cache, render_power_products


def finish(request, out, result, progress):
    progress("Packaging completed results", .98)
    atomic_json(out / "analysis-settings.json", request)
    result["files"] = sorted(path.name for path in out.iterdir() if path.is_file()) + ["manifest.json", "results.zip"]
    atomic_json(out / "manifest.json", result)
    with zipfile.ZipFile(out / "results.zip", "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in out.iterdir():
            if path.is_file() and path.name != "results.zip":
                bundle.write(path, path.name)
    atomic_json(out / "result.json", result)
    progress("Ready — completed aggregates and plots available", 1.)



def run(folder):
    folder = Path(folder)
    request = json.loads((folder / "request.json").read_text())
    out = folder / "out"
    out.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", force=True)
    # All UI figures are rendered to files, never shown or paused for input.
    plt.show = lambda *args, **kwargs: None
    warnings.filterwarnings("ignore", message="FigureCanvasAgg is non-interactive")
    settings = request["settings"]

    def progress(message, fraction):
        atomic_json(folder / "progress.json", {"message": message, "progress": fraction})

    if request.get("source_results"):
        from ui.opt_reanalysis import reanalyse
        progress("Updating plots from cached spectra", .05)
        result = reanalyse(request, out, progress)
        finish(request, out, result, progress)
        return
    native_arrays, native_labels = {}, []
    frequency_records = []
    velocity_power_records = []
    progress("Loading and processing calibration and SKY scans", .05)
    obs, sky_queue, cal_queue, _ = process_observation(
        request["directory"], request["observation"], profile=request["profile"],
        blacklist=request["excluded"], signal_displays=None,
        pipeline_config=request.get("pipeline_config"), cache_power_moments=True,
        progress_callback=lambda message, fraction: progress(message, fraction * .6))
    sky = [scan for scan in list(sky_queue.queue) if scan.get_status() == ScanState.COMPLETE]
    calibration = [scan for scan in list(cal_queue.queue) if scan.get_status() == ScanState.COMPLETE]
    if not sky:
        raise ValueError("No completed SKY aggregates were produced. Check exclusions, scan files and calibration in the log.")
    aggregates = sorted(calibration + sky, key=lambda scan: (scan.scan_model.tgt_idx, scan.scan_model.freq_scan, scan.scan_model.dig_id))
    power_records = physical_power_records(obs, request["excluded"])
    save_power_cache(power_records, out)
    result = {"observation": obs.obs_model.obs_id, "directory": request["directory"], "profile": request["profile"],
              "settings": settings, "excluded": request["excluded"], "aggregates": [], "views": {}, "warnings": []}
    if request.get("pipeline_config"):
        result["pipeline_config"] = request["pipeline_config"]
        atomic_json(out / "pipeline-config.json", request["pipeline_config"])
    incomplete = [scan.scan_model.scan_id for scan in list(sky_queue.queue) + list(cal_queue.queue) if scan.get_status() != ScanState.COMPLETE]
    if incomplete:
        result["warnings"].append(f"{len(incomplete)} incomplete aggregates were omitted; inspect the processing log.")
    for index, scan in enumerate(aggregates):
        model = scan.scan_model
        identifier = f"aggregate-{index:04d}"
        progress(f"Rendering completed aggregate {index + 1}/{len(aggregates)}", .6 + .25 * index / len(aggregates))
        target = obs.obs_model.get_target_by_index(model.tgt_idx)
        coordinates = target.sky_coord.galactic if target and target.sky_coord is not None else None
        contributing = [physical for group in obs.obs_model.target_scans for physical in group.scans
                        if physical.tgt_idx == model.tgt_idx and physical.freq_scan == model.freq_scan
                        and physical.dig_id == model.dig_id and physical.scan_type == model.scan_type
                        and physical.scan_id not in request["excluded"]]
        row = {"pipeline_baseline_applied": bool(request.get("pipeline_config") and model.scan_type != ScanType.LOAD
                                                     and baseline_applied(request["pipeline_config"], model.dig_id)),
               "id": identifier, "scan_id": model.scan_id, "target_id": model.tgt_idx,
               "freq_scan": model.freq_scan, "digitiser": model.dig_id, "kind": model.scan_type.name,
               "status": model.status.name, "frequency_mhz": model.center_freq / 1e6,
               "iterations": int(scan.get_loaded_seconds()), "input_scans": len(contributing),
               "integration_s": float(sum(entry.get("secs", 0) for key, entry in obs.int_data_arrays.items()
                                    if key[0] == model.tgt_idx and key[1] == model.freq_scan)),
               "longitude": float(coordinates.l.deg) if coordinates is not None else None,
               "latitude": float(coordinates.b.deg) if coordinates is not None else None,
               "usable_channels": int(valid_channels(scan.mpr, getattr(scan, "mpr_flags", None)).sum()),
               "channels": int(model.spectral_resolution),
               "views": {}}
        try:
            load = next((candidate for candidate in calibration if model.load_scan_id == candidate.scan_model.scan_id), None)
            signal = SignalDisplay(model.dig_id)
            signal.set_scan(scan, load)
            before = set(out.glob("*.png"))
            signal.save_scan_figure(str(out))
            created = set(out.glob("*.png")) - before
            if not created:
                raise ValueError("Signal renderer produced no figure")
            filename = f"{identifier}-signal.png"
            created.pop().rename(out / filename)
            row["views"]["signal"] = filename
            plt.close(signal.fig)
        except Exception as exc:
            logging.exception("Could not render signal view for %s", model.scan_id)
            result["warnings"].append(f"Signal view unavailable for {model.scan_id}: {exc}")
            plt.close("all")
        selected = ObsDisplay(obs.obs_model.obs_id)
        selected.set_scan([scan], obs.obs_model)
        if model.scan_type == ScanType.SKY:
            # Frequency requires native bins and powers, independently of LSRK metadata.
            frequency = (model.center_freq + np.fft.fftshift(np.fft.fftfreq(
                model.spectral_resolution, d=1/model.sample_rate))) / 1e6
            flags = getattr(scan, "mpr_flags", None)
            raw_power = masked_values(scan.mpr, flags) if flags is not None else np.ma.masked_invalid(scan.mpr)
            policy = qa_metric_settings(request.get("pipeline_config"), model.dig_id)
            native = dict(target_id=model.tgt_idx, freq_scan=model.freq_scan, raw_power=raw_power,
                          power=raw_power, channel_flags=flags, qa_settings=policy)
            galactic = coordinates
            filename = f"{identifier}-velocity.png"
            try:
                fig = selected.display_lsr(obs.obs_model, str(out / filename), blacklist=request["excluded"],
                                          observation_baseline=settings["baseline"], vmin=settings["baseline_vmin"],
                                          vmax=settings["baseline_vmax"], metric_settings=policy)
                if fig is not None:
                    row["views"]["velocity"] = filename
                    native = selected.lsr_spectra[0]
                    # LSRK order is the reverse of native increasing frequency.
                    frequency = np.sort(frequency)[::-1]
                    galactic = native["direction"].galactic
                    plt.close(fig)
                else:
                    reason = ("This observation has no recorded target sky coordinates or Alt/Az, so an LSRK velocity axis cannot be calculated."
                              if target is None or (getattr(target, "sky_coord", None) is None and getattr(target, "altaz", None) is None)
                              else "No LSRK spectrum could be generated with the recorded observing metadata and selected baseline limits.")
                    row.setdefault("view_errors", {})["velocity"] = reason
                    result["warnings"].append(f"Velocity unavailable for target {model.tgt_idx}: {reason}")
            except Exception as exc:
                row.setdefault("view_errors", {})["velocity"] = str(exc)
                result["warnings"].append(f"Velocity view unavailable for {model.scan_id}: {exc}")
                logging.exception("Could not render velocity view")
                plt.close("all")
            native["digitiser"] = model.dig_id
            if native.get("baseline") is not None:
                velocity_power_records.append({**native, "frequency_mhz": frequency,
                                               "frequency_correction": native["baseline"]})
            native_index = len(native_labels)
            native_arrays[f"{native_index}_power"] = native["raw_power"].filled(np.nan)
            if native.get("channel_flags") is not None:
                native_arrays[f"{native_index}_channel_flags"] = native["channel_flags"]
            native_arrays[f"{native_index}_frequency_mhz"] = frequency
            if native.get("velocity") is not None:
                native_arrays[f"{native_index}_velocity"] = native["velocity"]
            native_labels.append(dict(id=identifier, target_id=int(model.tgt_idx), freq_scan=int(model.freq_scan), digitiser=model.dig_id,
                                      qa_settings=policy, center_freq=float(model.center_freq),
                                      longitude=float(galactic.l.deg) if galactic is not None else None,
                                      latitude=float(galactic.b.deg) if galactic is not None else None,
                                      velocity_error=row.get("view_errors", {}).get("velocity")))
            try:
                frequency_filename = f"{identifier}-frequency.png"
                corrected_frequency = save_frequency_record({**native, "frequency_mhz": frequency}, out / frequency_filename,
                                      obs.obs_model.obs_id, settings["baseline"],
                                      fmin=settings.get("frequency_min_mhz"), fmax=settings.get("frequency_max_mhz"))
                row["views"]["frequency"] = frequency_filename
                frequency_records.append(corrected_frequency)
            except ValueError as exc:
                row.setdefault("view_errors", {})["frequency"] = str(exc)
                result["warnings"].append(f"Frequency view unavailable for {model.scan_id}: {exc}")
        result["aggregates"].append(row)

    progress("Rendering observation-wide plots and exports", .88)
    display = ObsDisplay(obs.obs_model.obs_id)
    display.set_scan(sky, obs.obs_model)
    save_aggregated_power_csv(obs.obs_model.obs_id, sky, str(out))
    power_basis = settings.get("total_power_basis", "frequency")
    render_power_products(power_records, velocity_power_records if power_basis == "velocity" else frequency_records,
                          result, out, settings["baseline"], basis=power_basis)
    if frequency_records:
        save_frequency_overlay(frequency_records, out / "frequency.png", obs.obs_model.obs_id, settings["baseline"])
        result["views"]["frequency"] = "frequency.png"
    else:
        result.setdefault("view_errors", {})["frequency"] = "No SKY spectra could be plotted with the selected Frequency limits and baseline settings."
    try:
        fig = display.display_lsr(obs.obs_model, str(out / "velocity.png"), blacklist=request["excluded"],
                                  csv_path=str(out / "velocity.csv"), observation_baseline=settings["baseline"],
                                  vmin=settings["baseline_vmin"], vmax=settings["baseline_vmax"], show_metrics=False)
        if fig is not None:
            result["views"]["velocity"] = "velocity.png"
            plt.close(fig)
            fig = display.display_lv(obs.obs_model, str(out / "lv.png"), contour_levels=settings["contours"],
                                     contour_smoothing=settings["contour_smoothing"],
                                     vmin=settings["baseline_vmin"], vmax=settings["baseline_vmax"])
            if fig is not None:
                result["views"]["lv"] = "lv.png"
                plt.close(fig)
        else:
            reason = next((row.get("view_errors", {}).get("velocity") for row in result["aggregates"]
                           if row.get("view_errors", {}).get("velocity")), "No usable LSRK velocity spectrum is available.")
            result.setdefault("view_errors", {})["velocity"] = reason
            result["view_errors"]["lv"] = f"The L–V view requires velocity coordinates. {reason}"
    except Exception as exc:
        logging.exception("Could not render observation velocity/L–V view")
        result["warnings"].append(f"Observation velocity/L–V view unavailable: {exc}")
        plt.close("all")
    if not any(row["views"].get("signal") for row in result["aggregates"]):
        raise ValueError("Signal displays could not be rendered. Inspect the processing log.")
    np.savez_compressed(out / "native-spectra.npz", **native_arrays)
    atomic_json(out / "native-spectra.json", native_labels)
    finish(request, out, result, progress)


def main():
    folder = Path(sys.argv[1])
    try:
        run(folder)
    except Exception as exc:
        traceback.print_exc()
        atomic_json(folder / "progress.json", {"message": "Processing failed", "progress": 0, "error": str(exc)})
        raise SystemExit(1)


if __name__ == "__main__":
    main()
