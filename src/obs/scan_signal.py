"""On-demand completed-scan rendering, isolated from acquisition and HTTP threads."""
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

from env.events import ResponseEvent
from obs.catalogue import scan_spectrum_file

logger = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scan-signal")
_lock = threading.Lock()
_jobs = {}


def sdp_settings_from_snapshot(payload):
    """SDP's AppModel already publishes launch arguments through TM."""
    if not isinstance(payload, dict) or payload.get("stale"):
        return {}
    sdp = (payload.get("telescope_model") or {}).get("sdp") or {}
    return {"profile": ((sdp.get("app") or {}).get("arguments") or {}).get("profile")}


def signal_product(store, observation, target, frequency, iteration, folder, sdp_settings_provider=None, *, generate=False):
    """Return an existing PNG, an explicit generation offer, or background status."""
    args = (store, observation, target, frequency, iteration, folder)
    image = scan_spectrum_file(*args, product="signal")
    if image.status == "success" or image.code != "not_found":
        return image
    spectrum = scan_spectrum_file(*args, product="spr")
    if spectrum.status != "success":
        return spectrum
    key = spectrum.payload["path"]
    with _lock:
        job = _jobs.get(key)
        if job is not None and not job.done():
            return ResponseEvent("success", "Generating signal display…", code="generating", payload={})
        if job is not None and not generate:
            if job.exception():
                return ResponseEvent("error", str(job.exception()), code="generation_failed")
            # Recheck after the worker published its image.
            return scan_spectrum_file(*args, product="signal")
        meta = Path(key).with_name(Path(key).name.removesuffix("-spr.csv") + "-meta.json")
        if meta.is_symlink() or not meta.is_file():
            return ResponseEvent("error", "Saved scan metadata is unavailable", code="not_found")
        saved = json.loads(meta.read_text())
        profile = saved.get("sdp_config_profile")
        source = "recorded with this scan"
        if not profile:
            settings = sdp_settings_provider() if sdp_settings_provider is not None else {}
            profile = settings.get("profile")
            source = "reported by SDP; the config profile was not recorded with this older scan"
        if not isinstance(profile, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", profile):
            return ResponseEvent("error", "Cannot identify SDP's config profile. Refresh the TM snapshot while SDP is connected, then retry.",
                                 code="sdp_profile_unavailable")
        from models.pipeline import PipelineConfig
        profile_dir = Path(__file__).resolve().parents[1] / "config" / profile
        if not (profile_dir / "PipelineConfig.json").is_file():
            return ResponseEvent("error", f"SDP config profile '{profile}' has no PipelineConfig.json on this server.",
                                 code="sdp_profile_unavailable")
        config = PipelineConfig.load_from_disk(input_dir=str(profile_dir), filename="PipelineConfig.json")
        if not generate:
            return ResponseEvent("error", f"Signal display image not available. Generate it using the SDP's current config profile '{profile}'?",
                                 code="generation_available", payload={"profile": profile, "profile_source": source})
        if any(not pending.done() for pending in _jobs.values()):
            return ResponseEvent("error", "Another signal image is generating. Please try again shortly.", code="generation_busy")
        request = {"root": str(Path(store).expanduser().resolve()), "spr": key,
                   "pipeline": config.to_dict(), "profile": profile}
        # Bound retained job state; running jobs are never removed.
        for old in list(_jobs):
            if len(_jobs) < 128:
                break
            if _jobs[old].done():
                del _jobs[old]
        _jobs[key] = _executor.submit(_run_worker, request)
    return ResponseEvent("success", "Generating signal display…", code="generating", payload={})


def _run_worker(request):
    environment = dict(os.environ, MPLBACKEND="Agg", PYTHONUNBUFFERED="1")
    src = str(Path(__file__).resolve().parents[1])
    environment["PYTHONPATH"] = src + os.pathsep + environment.get("PYTHONPATH", "")
    try:
        result = subprocess.run([sys.executable, "-m", "obs.scan_signal"], input=json.dumps(request),
                                text=True, capture_output=True, env=environment, timeout=180)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("Signal generation timed out. You can retry.") from error
    if result.returncode:
        logger.error("Signal worker failed: %s", result.stderr[-8000:])
        raise RuntimeError("Signal generation failed. Check the web server log and saved calibration files.")


def render(request):
    """Reprocess one physical scan's SPR with the shared pipeline and renderer."""
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    from queue import Queue
    from models.pipeline import PipelineConfig
    from models.scan import ScanModel, ScanState, ScanType
    from obs.scan import Scan
    from sdp.pipeline.pipeline_factory import ProcessingPipelineFactory
    from sdp.signal_display import SignalDisplay

    root = Path(request["root"]).resolve(strict=True)
    spr = Path(request["spr"])

    def checked(path):
        relative = path.relative_to(root)
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink() or part == "..":
                raise ValueError("Linked scan files are not supported")
        return path

    checked(spr)
    prefix = spr.name.removesuffix("-spr.csv")
    meta = checked(spr.with_name(prefix + "-meta.json"))
    model = ScanModel.from_dict(json.loads(meta.read_text()))
    if model.status != ScanState.COMPLETE:
        raise ValueError("Only completed scans can be rendered")
    destination = checked(spr.with_name(prefix + "-sigfig.png"))
    if destination.is_file():
        return
    factory = ProcessingPipelineFactory(PipelineConfig.from_dict(request["pipeline"]))
    sky, calibration = Queue(), Queue()

    def load_saved(saved_prefix, expected_id):
        scan = Scan.from_disk(saved_prefix, str(spr.parent))
        if scan is None or scan.scan_model.scan_id != expected_id:
            raise ValueError("Saved scan could not be loaded or its identity differs")
        # Bind processing steps to the actual loaded scan, including its QA.
        scan.set_pipeline(factory.create_pipeline(scan, sky, calibration))
        if not scan.process_pipeline():
            raise ValueError("Saved scan processing failed")
        return scan

    load = None
    # Prefer the exact calibration recorded at acquisition, rather than a later LOAD.
    if model.scan_type != ScanType.LOAD and model.load_scan_id:
        for candidate in spr.parent.glob("*-meta.json"):
            checked(candidate)
            try:
                saved = ScanModel.from_dict(json.loads(candidate.read_text()))
            except (ValueError, TypeError):
                continue
            if saved.scan_id != model.load_scan_id:
                continue
            if saved.status != ScanState.COMPLETE or saved.scan_type != ScanType.LOAD:
                raise ValueError("Recorded calibration is not a completed LOAD scan")
            checked(candidate.with_name(candidate.name.removesuffix("-meta.json") + "-spr.csv"))
            load = load_saved(candidate.name.removesuffix("-meta.json"), saved.scan_id)
            if not model.equivalent(load.scan_model):
                raise ValueError("Recorded calibration could not be loaded or does not match")
            calibration.put(load)
            break
        if load is None:
            raise ValueError("Recorded LOAD scan is unavailable; cannot reproduce calibration")
    scan = load_saved(prefix, model.scan_id)
    if scan is None or scan.get_loaded_seconds() != model.duration:
        raise ValueError("Completed scan data is missing or incomplete")
    signal = SignalDisplay(model.dig_id)
    plt.show = lambda *args, **kwargs: None
    signal.set_scan(scan, load, iq_available=False)
    signal.fig.text(.5, .01, f"Generated from saved SPR · SDP config profile: {request['profile']}", ha="center", fontsize=8)
    try:
        # Publish only a complete PNG, on the same filesystem as the archive.
        with tempfile.TemporaryDirectory(prefix=".signal-", dir=spr.parent) as temporary:
            signal.save_scan_figure(temporary)
            rendered = list(Path(temporary).glob("*.png"))
            if len(rendered) != 1:
                raise ValueError("Signal renderer did not produce one PNG")
            checked(destination)
            rendered[0].replace(destination)
    finally:
        plt.close(signal.fig)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    render(json.load(sys.stdin))
