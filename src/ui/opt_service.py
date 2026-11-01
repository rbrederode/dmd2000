"""Local-only HTTP bridge between the frontend prototype and OPT workers.

Run from the repository root:
    PYTHONPATH=src python -m ui.opt_service --data-root ~/samples
"""
from __future__ import annotations

import argparse
import atexit
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import uuid

from flask import Flask, abort, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from models.obs import ObsModel
from util.convert import datetime_to_utc

SRC = Path(__file__).resolve().parents[1]
FRONTEND = SRC / "ui/frontend_prototype"


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def observation_summary(directory, obs_id):
    model = ObsModel.load_from_disk(input_dir=str(directory), filename=f"{obs_id}-obs.json")
    rows = []
    for group in model.target_scans:
        for scan in group.scans:
            target = model.get_target_by_index(scan.tgt_idx)
            sky = getattr(target, "sky_coord", None)
            galactic = sky.galactic if sky is not None else None
            rows.append({"id": scan.scan_id, "target_id": scan.tgt_idx, "freq_scan": scan.freq_scan,
                         "iteration": scan.scan_iter, "digitiser": scan.dig_id, "kind": scan.scan_type.name,
                         "status": scan.status.name, "duration_s": scan.duration,
                         "frequency_mhz": scan.center_freq / 1e6,
                         "longitude": float(galactic.l.deg) if galactic is not None else None,
                         "latitude": float(galactic.b.deg) if galactic is not None else None})
    return {"id": model.obs_id, "dish": model.dsh_id, "scans": rows}


def observation_catalogue_entry(directory, obs_id):
    """Summarise metadata only; completed time includes every completed type."""
    model = ObsModel.load_from_disk(input_dir=str(directory), filename=f"{obs_id}-obs.json")
    scans = [scan for group in model.target_scans for scan in group.scans]
    completed = [scan for scan in scans if scan.status.name == "COMPLETE"]
    counts = {}
    for scan in completed:
        counts[scan.scan_type.name] = counts.get(scan.scan_type.name, 0) + 1
    return {"id": obs_id, "name": model.title.strip() or obs_id, "dish": model.dsh_id,
            "completed_scans": len(completed), "total_scans": len(scans),
            "completed_types": counts,
            "completed_integration_s": float(sum(scan.duration for scan in completed)),
            "started_at": datetime_to_utc(model.start_dt).isoformat(), "error": None}


def validate_settings(data):
    import math
    if not isinstance(data, dict):
        raise ValueError("Analysis settings must be a JSON object")
    settings = {"baseline": data.get("baseline", "linear"),
                "baseline_vmin": float(data.get("baseline_vmin", -150)),
                "baseline_vmax": float(data.get("baseline_vmax", 150)),
                "contour_smoothing": float(data.get("contour_smoothing", 2)),
                "contours": data.get("contours", None)}
    if settings["baseline"] not in ("none", "constant", "linear"):
        raise ValueError("Choose a linear, constant or no baseline")
    if not all(math.isfinite(settings[k]) for k in ("baseline_vmin", "baseline_vmax", "contour_smoothing")):
        raise ValueError("Analysis settings must be finite numbers")
    if settings["baseline_vmin"] >= settings["baseline_vmax"] or settings["contour_smoothing"] < 0:
        raise ValueError("Check baseline limits and contour smoothing")
    if settings["contours"] is not None:
        if not isinstance(settings["contours"], list):
            raise ValueError("Contours must be a list of power levels")
        settings["contours"] = [float(v) for v in settings["contours"]]
        levels = settings["contours"]
        if any(not math.isfinite(v) or v <= 0 for v in levels) or any(a >= b for a, b in zip(levels, levels[1:])):
            raise ValueError("Contour levels must be positive, finite and increasing")
    return settings


class Jobs:
    def __init__(self, cache):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.jobs = {}
        self.lock = threading.RLock()

    def status(self, job):
        process = job["process"]
        code = process.poll()
        result_path = job["folder"] / "out/result.json"
        state = "cancelled" if job["cancelled"] else ("running" if code is None else ("ready" if code == 0 and result_path.exists() else "failed"))
        progress_path = job["folder"] / "progress.json"
        progress = json.loads(progress_path.read_text()) if progress_path.exists() else {"message": "Starting OPT", "progress": 0}
        payload = {"id": job["id"], "state": state, **progress}
        if state == "ready":
            payload["result"] = json.loads(result_path.read_text())
        log_path = job["folder"] / "processing.log"
        if log_path.exists():
            with log_path.open("rb") as stream:
                stream.seek(max(0, log_path.stat().st_size - 30000))
                payload["log"] = stream.read().decode("utf-8", errors="replace")
        if state == "failed":
            payload["message"] = progress.get("error") or "Processing failed. See the processing log for details."
        return payload

    def start(self, configuration):
        with self.lock:
            if any(job["process"].poll() is None for job in self.jobs.values()):
                raise RuntimeError("An observation is already processing; wait or cancel it first")
            job_id = uuid.uuid4().hex
            folder = self.cache / job_id
            folder.mkdir()
            atomic_json(folder / "request.json", configuration)
            environment = dict(os.environ, MPLBACKEND="Agg", PYTHONUNBUFFERED="1")
            environment["PYTHONPATH"] = str(SRC) + os.pathsep + environment.get("PYTHONPATH", "")
            environment["MPLCONFIGDIR"] = str(self.cache / "matplotlib")
            with (folder / "processing.log").open("w") as log:
                process = subprocess.Popen([sys.executable, "-m", "ui.opt_worker", str(folder)],
                                           cwd=SRC.parent, env=environment, stdin=subprocess.DEVNULL,
                                           stdout=log, stderr=subprocess.STDOUT)
            job = {"id": job_id, "folder": folder, "process": process, "cancelled": False}
            self.jobs[job_id] = job
            return self.status(job)

    def cancel(self, job):
        with self.lock:
            if job["process"].poll() is None:
                job["cancelled"] = True
                job["process"].terminate()
            return self.status(job)

    def close(self):
        for job in self.jobs.values():
            if job["process"].poll() is None:
                job["process"].terminate()
                try:
                    job["process"].wait(timeout=5)
                except subprocess.TimeoutExpired:
                    job["process"].kill()
                    job["process"].wait()


def create_app(data_roots=None, cache_dir=None):
    roots = [Path(p).expanduser().resolve() for p in (data_roots or [Path.home() / "samples"])]
    app = Flask(__name__, static_folder=str(FRONTEND), static_url_path="")
    app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024
    jobs = Jobs(cache_dir or tempfile.mkdtemp(prefix="dmd2000-opt-ui-"))
    app.extensions["opt_jobs"] = jobs
    atexit.register(jobs.close)

    @app.before_request
    def local_only():
        if request.host.split(":")[0] not in ("127.0.0.1", "localhost"):
            abort(403, "This service only accepts localhost requests")
        if request.method == "POST":
            if request.headers.get("Origin") and request.headers["Origin"] != request.host_url.rstrip("/"):
                abort(403, "Cross-origin requests are not accepted")
            if not request.is_json:
                abort(415, "Use application/json")
            if not isinstance(request.get_json(silent=True), dict):
                raise ValueError("Send a JSON object with the requested observation or job")

    @app.errorhandler(ValueError)
    @app.errorhandler(TypeError)
    def invalid(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(HTTPException)
    def http_error(error):
        if request.path.startswith("/api/opt/"):
            return jsonify(error=error.description), error.code
        return error

    @app.errorhandler(OSError)
    def unavailable(error):
        return jsonify(error=str(error)), 400

    def directory(value):
        path = Path(value or roots[0]).expanduser().resolve()
        if not any(path == root or root in path.parents for root in roots):
            raise ValueError("Choose an observation directory inside a configured data root")
        if not path.is_dir():
            raise ValueError("Observation directory does not exist")
        return path

    def load_configuration(data):
        folder = directory(data.get("directory"))
        obs_id = data.get("observation", "")
        if not isinstance(obs_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:+-]+", obs_id):
            raise ValueError("Choose a valid observation ID without the -obs.json suffix")
        summary = observation_summary(folder, obs_id)
        return folder, obs_id, summary

    def get_job(job_id):
        job = jobs.jobs.get(job_id)
        if job is None:
            abort(404)
        return job

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    @app.get("/api/opt/config")
    def configuration():
        profiles = sorted(path.parent.name for path in (SRC / "config").glob("*/PipelineConfig.json"))
        folders = []
        for root in roots:
            if root.is_dir():
                folders += [str(root)] + [str(p) for p in sorted(root.iterdir()) if p.is_dir() and not p.is_symlink()]
        return jsonify(roots=[str(p) for p in roots], directories=folders, profiles=profiles)

    @app.get("/api/opt/health")
    def health():
        return jsonify(status="ok")

    @app.get("/api/opt/observations")
    def observations():
        folder = directory(request.args.get("directory"))
        ids = sorted(p.name.removesuffix("-obs.json") for p in folder.glob("*-obs.json") if p.is_file())
        entries = []
        for obs_id in ids:
            try:
                entries.append(observation_catalogue_entry(folder, obs_id))
            except Exception:
                app.logger.warning("Could not read observation metadata: %s", obs_id, exc_info=True)
                entries.append({"id": obs_id, "name": obs_id, "dish": None,
                                "error": "Observation metadata could not be read"})
        entries.sort(key=lambda item: (not bool(item.get("error")), item.get("started_at", ""), item["id"]), reverse=True)
        return jsonify(directory=str(folder), observations=ids, entries=entries)

    @app.post("/api/opt/open")
    def open_observation():
        folder, obs_id, summary = load_configuration(request.get_json())
        return jsonify(directory=str(folder), **summary)

    @app.post("/api/opt/process")
    def process():
        data = request.get_json()
        folder, obs_id, summary = load_configuration(data)
        profile = data.get("profile", "default")
        if not isinstance(profile, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", profile) or not (SRC / "config" / profile / "PipelineConfig.json").is_file():
            raise ValueError("Choose an available processing profile")
        excluded = data.get("excluded", [])
        valid_ids = {scan["id"] for scan in summary["scans"]}
        if not isinstance(excluded, list) or any(not isinstance(scan_id, str) or scan_id not in valid_ids for scan_id in excluded):
            raise ValueError("Exclusions must refer to scans in this observation")
        if not any(scan["kind"] == "SKY" and scan["status"] == "COMPLETE" and scan["id"] not in excluded for scan in summary["scans"]):
            raise ValueError("Include at least one completed SKY scan")
        settings = validate_settings(data.get("settings", {}))
        try:
            payload = jobs.start({"directory": str(folder), "observation": obs_id, "profile": profile,
                                  "excluded": excluded, "settings": settings})
        except RuntimeError as exc:
            return jsonify(error=str(exc)), 409
        return jsonify(payload), 202

    @app.post("/api/opt/reanalyse")
    def reanalyse():
        data = request.get_json()
        source = get_job(data.get("job"))
        if jobs.status(source)["state"] != "ready" or not (source["folder"] / "out/native-spectra.npz").exists():
            raise ValueError("Process the observation first to cache its spectra")
        configuration = json.loads((source["folder"] / "request.json").read_text())
        configuration["settings"] = validate_settings(data.get("settings", {}))
        configuration["source_results"] = str(source["folder"] / "out")
        try:
            return jsonify(jobs.start(configuration)), 202
        except RuntimeError as exc:
            return jsonify(error=str(exc)), 409

    @app.get("/api/opt/jobs/<job_id>")
    def job_status(job_id):
        return jsonify(jobs.status(get_job(job_id)))

    @app.post("/api/opt/jobs/<job_id>/cancel")
    def cancel(job_id):
        return jsonify(jobs.cancel(get_job(job_id)))

    @app.get("/api/opt/jobs/<job_id>/files/<name>")
    def artifact(job_id, name):
        job = get_job(job_id)
        status = jobs.status(job)
        if status["state"] != "ready" or name not in status["result"]["files"]:
            abort(404)
        return send_from_directory(job["folder"] / "out", name, as_attachment=request.args.get("download") == "1")

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", action="append", help="Permitted observation root; repeat for more roots (default ~/samples)")
    parser.add_argument("--cache-dir", help="Directory for UI result sessions; defaults to a temporary directory")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    app = create_app(args.data_root, args.cache_dir)
    print(f"OPT workspace: http://127.0.0.1:{args.port}/")
    print(f"Result sessions: {app.extensions['opt_jobs'].cache}")
    try:
        app.run(host="127.0.0.1", port=args.port, debug=False, threaded=True)
    finally:
        app.extensions["opt_jobs"].close()


if __name__ == "__main__":
    main()
