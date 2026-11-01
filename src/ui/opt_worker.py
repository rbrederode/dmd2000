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
from obs.obs_display import ObsDisplay
from obs.opt import process_observation, save_aggregated_power_csv
from sdp.channel_mask import valid_channels
from sdp.signal_display import SignalDisplay
from ui.opt_service import atomic_json


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
    progress("Loading and processing calibration and SKY scans", .05)
    obs, sky_queue, cal_queue, _ = process_observation(
        request["directory"], request["observation"], profile=request["profile"],
        blacklist=request["excluded"], signal_displays=None,
        progress_callback=lambda message, fraction: progress(message, fraction * .6))
    sky = [scan for scan in list(sky_queue.queue) if scan.get_status() == ScanState.COMPLETE]
    calibration = [scan for scan in list(cal_queue.queue) if scan.get_status() == ScanState.COMPLETE]
    if not sky:
        raise ValueError("No completed SKY aggregates were produced. Check exclusions, scan files and calibration in the log.")
    aggregates = sorted(calibration + sky, key=lambda scan: (scan.scan_model.tgt_idx, scan.scan_model.freq_scan, scan.scan_model.dig_id))
    result = {"observation": obs.obs_model.obs_id, "directory": request["directory"], "profile": request["profile"],
              "settings": settings, "excluded": request["excluded"], "aggregates": [], "views": {}, "warnings": []}
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
        row = {"id": identifier, "scan_id": model.scan_id, "target_id": model.tgt_idx,
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
        if model.scan_type == ScanType.SKY:
            selected = ObsDisplay(obs.obs_model.obs_id)
            selected.set_scan([scan], obs.obs_model)
            filename = f"{identifier}-velocity.png"
            try:
                fig = selected.display_lsr(obs.obs_model, str(out / filename), blacklist=request["excluded"],
                                          observation_baseline=settings["baseline"], baseline_vmin=settings["baseline_vmin"],
                                          baseline_vmax=settings["baseline_vmax"])
                if fig is not None:
                    row["views"]["velocity"] = filename
                    native = selected.lsr_spectra[0]
                    native_index = len(native_labels)
                    native_arrays[f"{native_index}_velocity"] = native["velocity"]
                    native_arrays[f"{native_index}_power"] = native["raw_power"].filled(np.nan)
                    galactic = native["direction"].galactic
                    native_labels.append({"id": identifier, "target_id": int(model.tgt_idx),
                                          "freq_scan": int(model.freq_scan), "center_freq": float(model.center_freq),
                                          "longitude": float(galactic.l.deg), "latitude": float(galactic.b.deg)})
                    plt.close(fig)
            except Exception as exc:
                result["warnings"].append(f"Velocity view unavailable for {model.scan_id}: {exc}")
                logging.exception("Could not render velocity view")
                plt.close("all")
        result["aggregates"].append(row)

    progress("Rendering observation-wide plots and exports", .88)
    display = ObsDisplay(obs.obs_model.obs_id)
    display.set_scan(sky, obs.obs_model)
    save_aggregated_power_csv(obs.obs_model.obs_id, sky, str(out))
    display.save_integrated_total_power(str(out / "timeline.png"))
    result["views"]["timeline"] = "timeline.png"
    display.display()
    display.fig.savefig(out / "overview.png", dpi=150, bbox_inches="tight")
    result["views"]["overview"] = "overview.png"
    plt.close(display.fig)
    try:
        fig = display.display_lsr(obs.obs_model, str(out / "velocity.png"), blacklist=request["excluded"],
                                  csv_path=str(out / "velocity.csv"), observation_baseline=settings["baseline"],
                                  baseline_vmin=settings["baseline_vmin"], baseline_vmax=settings["baseline_vmax"])
        if fig is not None:
            result["views"]["velocity"] = "velocity.png"
            plt.close(fig)
            fig = display.display_lv(obs.obs_model, str(out / "lv.png"), contour_levels=settings["contours"],
                                     contour_smoothing=settings["contour_smoothing"])
            if fig is not None:
                result["views"]["lv"] = "lv.png"
                plt.close(fig)
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
