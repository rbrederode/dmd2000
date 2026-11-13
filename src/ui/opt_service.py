"""Local-only HTTP bridge between the frontend prototype and OPT workers.

Run from the repository root:
    PYTHONPATH=src python -m ui.opt_service --data-root ~/samples
"""
from __future__ import annotations

import argparse
import atexit
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import uuid
from urllib.parse import urlsplit
import requests

from flask import Flask, abort, jsonify, request, send_from_directory, send_file, Response, stream_with_context
from werkzeug.exceptions import HTTPException

from models.obs import ObsModel
from util.convert import datetime_to_utc
from ui.opt_pipeline import pipeline_snapshot, editable_pipeline, available_pipeline_steps
from ui.pipeline_help import PIPELINE_OVERVIEW
from ui.opt_targets import target_display_metadata

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
            rows.append({"id": scan.scan_id, "target_id": scan.tgt_idx, "freq_scan": scan.freq_scan,
                         "iteration": scan.scan_iter, "digitiser": scan.dig_id, "kind": scan.scan_type.name,
                         "status": scan.status.name, "duration_s": scan.duration,
                         "frequency_mhz": scan.center_freq / 1e6,
                         **target_display_metadata(target)})
    from web.model_snapshot import redact_credentials
    return {"id": model.obs_id, "file_id": obs_id, "dish": model.dsh_id, "scans": rows,
            "metadata": redact_credentials(model.to_dict())}


def observation_catalogue_entry(directory, obs_id):
    """Summarise metadata only; completed time includes every completed type."""
    model = ObsModel.load_from_disk(input_dir=str(directory), filename=f"{obs_id}-obs.json")
    scans = [scan for group in model.target_scans for scan in group.scans]
    completed = [scan for scan in scans if scan.status.name == "COMPLETE"]
    counts = {}
    for scan in completed:
        counts[scan.scan_type.name] = counts.get(scan.scan_type.name, 0) + 1
    metadata = model.to_dict()
    header = {key: metadata.get(key) for key in ("title", "obs_state", "scheduling_block_start", "scheduling_block_end",
                                                "total_integration_time", "estimated_observation_duration", "created", "user_email")}
    return {**header, "id": obs_id, "name": model.title.strip() or obs_id, "dish": model.dsh_id,
            "completed_scans": len(completed), "total_scans": len(scans),
            "completed_types": counts,
            "completed_integration_s": float(sum(scan.duration for scan in completed)),
            "completed": bool(scans) and len(completed) == len(scans),
            "started_at": datetime_to_utc(model.start_dt).isoformat(), "error": None}


def validate_settings(data):
    import math
    if not isinstance(data, dict):
        raise ValueError("Analysis settings must be a JSON object")
    settings = {"baseline": data.get("baseline", "none"),
                "baseline_vmin": None if data.get("baseline_vmin") in (None, "") else float(data["baseline_vmin"]),
                "baseline_vmax": None if data.get("baseline_vmax") in (None, "") else float(data["baseline_vmax"]),
                "contour_smoothing": float(data.get("contour_smoothing", 2)),
                "contours": data.get("contours", None)}
    if "total_power_basis" in data:
        if data["total_power_basis"] not in ("frequency", "velocity"):
            raise ValueError("Total Power baseline source must be frequency or velocity")
        settings["total_power_basis"] = data["total_power_basis"]
    for key in ("frequency_min_mhz", "frequency_max_mhz"):
        value = data.get(key)
        try:
            settings[key] = None if value is None else float(value)
        except (ValueError, TypeError) as exc:
            raise ValueError("Frequency limits must be positive finite MHz values or blank") from exc
        if settings[key] is not None and (not math.isfinite(settings[key]) or settings[key] <= 0):
            raise ValueError("Frequency limits must be positive finite MHz values or blank")
    if (settings["frequency_min_mhz"] is not None and settings["frequency_max_mhz"] is not None
            and settings["frequency_min_mhz"] >= settings["frequency_max_mhz"]):
        raise ValueError("Frequency minimum must be less than maximum")
    if settings["baseline"] not in ("none", "constant", "linear", "flatten"):
        raise ValueError("Choose a linear, constant, flatten or no baseline")
    if not all(settings[k] is None or math.isfinite(settings[k]) for k in ("baseline_vmin", "baseline_vmax", "contour_smoothing")):
        raise ValueError("Analysis settings must be finite numbers")
    low, high = settings["baseline_vmin"], settings["baseline_vmax"]
    if (low is not None and high is not None and low >= high) or settings["contour_smoothing"] < 0:
        raise ValueError("Check baseline limits and contour smoothing")
    if settings["baseline"] != "none" and ((low is not None and low >= 0) or (high is not None and high <= 0)):
        raise ValueError("Automatic baseline windows require velocity minimum < 0 < maximum")
    if settings["contours"] is not None:
        if not isinstance(settings["contours"], list):
            raise ValueError("Contours must be a list of power levels")
        settings["contours"] = [float(v) for v in settings["contours"]]
        levels = settings["contours"]
        if any(not math.isfinite(v) or v <= 0 for v in levels) or any(a >= b for a, b in zip(levels, levels[1:])):
            raise ValueError("Contour levels must be positive, finite and increasing")
    return settings


def validate_lab_sweep(data):
    """Validate offset grids before starting a background worker."""
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ValueError("LAB sweep requires longitude and latitude offset ranges")
    ranges, candidates = {}, 1
    for axis, limit in (("longitude", 180), ("latitude", 90)):
        values = data.get(axis)
        if not isinstance(values, list) or len(values) != 3:
            raise ValueError(f"LAB {axis} sweep needs minimum, maximum and step")
        try:
            low, high, step = map(float, values)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("LAB sweep values must be finite numbers") from exc
        if (not all(math.isfinite(v) for v in (low, high, step)) or step <= 0
                or low > high or low < -limit or high > limit):
            raise ValueError(f"Invalid LAB {axis} offsets or step (allowed offsets: {-limit} to {limit} degrees)")
        intervals = (high - low) / step
        if not math.isfinite(intervals) or intervals > 2500:
            raise ValueError("LAB sweep exceeds 2500 candidates; increase the step or narrow the ranges")
        candidates *= int(math.floor(intervals + 1e-9)) + 1
        ranges[axis] = [low, high, step]
    if candidates > 2500:
        raise ValueError("LAB sweep exceeds 2500 candidates; increase the step or narrow the ranges")
    return ranges


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


def create_app(data_roots=None, cache_dir=None, web_url=None, web_token=None, profile_provider=None):
    roots = [Path(p).expanduser().resolve() for p in (data_roots or [Path.home() / "samples"])]
    app = Flask(__name__, static_folder=str(FRONTEND), static_url_path="")
    app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024
    web_url = (web_url or os.getenv('WEB_API_URL', 'http://127.0.0.1:8000')).rstrip('/')
    address = urlsplit(web_url)
    if address.scheme not in ('http', 'https') or not address.hostname or address.username or address.password or address.query or address.fragment or address.path:
        raise ValueError('WEB URL must be an HTTP(S) server address without credentials or a path')
    web_token = web_token if web_token is not None else os.getenv('WEBHOOK_SECRET')
    jobs = Jobs(cache_dir or tempfile.mkdtemp(prefix="dmd2000-opt-ui-"))
    app.extensions["opt_jobs"] = jobs
    atexit.register(jobs.close)

    def diagnostic_profile():
        if profile_provider is not None:
            return profile_provider()
        # The optional standalone console gets the same selection from WEB.
        if not web_token:
            abort(503, 'WEB authentication is not configured')
        try:
            upstream = requests.request('GET', web_url + '/api/profile',
                headers={'X-Webhook-Token': web_token}, timeout=(3, 10), allow_redirects=False)
            body = upstream.json()
            if upstream.status_code != 200 or not isinstance(body, dict) or body.get('status') != 'success' or not isinstance(body.get('payload'), dict):
                abort(502, 'Could not load the selected WEB profile')
            return body['payload']
        except (requests.exceptions.RequestException, ValueError):
            abort(502, 'Could not load the selected WEB profile')

    from ui.diagnostics import register_diagnostics
    register_diagnostics(app, diagnostic_profile)

    from ui import commands

    @app.get('/api/commands')
    def application_commands():
        application = request.args.get('app', '')
        if application not in commands.APPLICATIONS:
            return jsonify(status='error', message='Choose an application type'), 400
        try:
            return jsonify(status='success', payload={'commands': commands.choices(application)})
        except (OSError, ValueError):
            return jsonify(status='error', message='Command registry is unavailable'), 503

    @app.post('/api/commands')
    def application_command():
        data = request.get_json()
        if not isinstance(data, dict) or set(data) - {'app', 'cmd', 'value'}:
            return jsonify(status='error', message='Supply app, cmd and optional value'), 400
        target, command = data.get('app'), data.get('cmd')
        value = data.get('value')
        if (not isinstance(target, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', target)
                or not isinstance(command, str)
                or not re.fullmatch(r'[a-z][a-z0-9_-]*', command)
                or (command in ('trace', 'debug') and value not in (None, 'ON', 'OFF'))
                or (command == 'stop' and (target.lower() != 'dm' or not isinstance(value, str)
                    or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value)))
                or (command == 'resync' and value is not None)):
            return jsonify(status='error', message='Choose a valid application, command and value'), 400
        try:
            commands.validate(target, command, value)
        except ValueError as exc:
            return jsonify(status='error', message=str(exc)), 400
        except OSError:
            return jsonify(status='error', message='Command registry is unavailable'), 503
        if not web_token:
            return jsonify(status='error', message='WEB authentication is not configured'), 503
        message = {'app': target, 'cmd': command}
        if value is not None:
            message['value'] = value
        try:
            upstream = requests.request('POST', web_url + '/webhook',
                headers={'X-Webhook-Token': web_token},
                json={'event': 'alston-rt.ui.cmd', 'message': message},
                timeout=(3, 30), allow_redirects=False)
            body = upstream.json()
            if (not isinstance(body, dict) or body.get('status') not in ('success', 'error')
                    or not isinstance(body.get('message'), str)):
                raise ValueError('Invalid command response')
            return jsonify(body), upstream.status_code
        except (requests.exceptions.RequestException, ValueError):
            return jsonify(status='error', code='outcome_unknown',
                message='No valid command response was received. The outcome is unknown; check the application before retrying.'), 502

    @app.post('/api/observation-control/<obs_id>')
    def observation_control(obs_id):
        data = request.get_json()
        operations = {'delete': 'ObservationDelete', 'abort': 'ObservationAbort', 'restart': 'ObservationRestart'}
        if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,255}', obs_id) or not isinstance(data, dict)
                or set(data) != {'operation'} or not isinstance(data.get('operation'), str) or data['operation'] not in operations):
            return jsonify(status='error', message='Supply delete, abort or restart for an observation ID'), 400
        if not web_token:
            return jsonify(status='error', message='WEB authentication is not configured'), 503
        try:
            upstream = requests.request('POST', web_url + '/webhook', headers={'X-Webhook-Token': web_token},
                json={'event': 'alston-rt.ui.oet', 'message': {'_type': operations[data['operation']], 'obs_id': obs_id}},
                timeout=(3, 30), allow_redirects=False)
            body = upstream.json()
            if not isinstance(body, dict) or body.get('status') not in ('success', 'error') or not isinstance(body.get('message'), str):
                raise ValueError('Invalid observation control response')
            return jsonify(body), upstream.status_code
        except (requests.exceptions.RequestException, ValueError):
            return jsonify(status='error', code='outcome_unknown',
                message='No valid response received. The outcome is unknown; refresh the schedule before retrying.'), 502

    @app.post('/api/dish-control/<dish_id>')
    def dish_control(dish_id):
        """Live dish settings, distinct from file-backed configuration editing."""
        from models.dsh import DishMode, Capability
        data = request.get_json()
        if (not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', dish_id) or not isinstance(data, dict)
                or len(data) != 1 or not set(data) <= {'mode', 'capability'}):
            return jsonify(status='error', message='Supply one mode or capability for a dish ID'), 400
        field, value = next(iter(data.items()))
        choices = DishMode if field == 'mode' else Capability
        if not isinstance(value, str) or value not in choices.__members__:
            return jsonify(status='error', message='Unknown dish ' + field), 400
        if not web_token:
            return jsonify(status='error', message='WEB authentication is not configured'), 503
        try:
            upstream = requests.request('POST', web_url + '/webhook',
                headers={'X-Webhook-Token': web_token},
                json={'event': 'alston-rt.ui.dsh', 'message': {'dsh_id': dish_id, field: value}},
                timeout=(3, 30), allow_redirects=False)
            body = upstream.json()
            if (not isinstance(body, dict) or body.get('status') not in ('success', 'error')
                    or not isinstance(body.get('message'), str)):
                raise ValueError('Invalid configuration response')
            return jsonify(body), upstream.status_code
        except (requests.exceptions.RequestException, ValueError):
            return jsonify(status='error', code='outcome_unknown',
                message='No valid response received. The outcome is unknown; check the dish before retrying.'), 502

    @app.post('/api/digitiser-control/<dig_id>')
    def digitiser_control(dig_id):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', dig_id):
            return jsonify(status='error', message='Choose a valid digitiser ID'), 400
        settings = request.get_json()
        numeric = {'gain': (0, 100, False), 'center_freq': (0, None, False),
            'sample_rate': (0, None, False), 'bandwidth': (0, None, False),
            'freq_correction': (-1000, 1000, True), 'channels': (0, None, True), 'scan_duration': (0, None, True)}
        if not isinstance(settings, dict) or not settings or set(settings) - numeric.keys() - {'load_active', 'scanning'}:
            return jsonify(status='error', message='Supply digitiser settings or a scanning state'), 400
        for key, value in settings.items():
            if key in ('load_active', 'scanning'):
                valid = type(value) is bool
            else:
                lower, upper, integer = numeric[key]
                valid = (type(value) in (int, float) and math.isfinite(value) and value >= lower
                    and (upper is None or value <= upper) and (not integer or float(value).is_integer())
                    and (key in ('gain', 'freq_correction') or value > 0))
            if not valid:
                return jsonify(status='error', message='Invalid value for ' + key, code='invalid_request'), 400
        if not web_token:
            return jsonify(status='error', message='WEB authentication is not configured'), 503
        try:
            upstream = requests.request('POST', web_url + '/webhook',
                headers={'X-Webhook-Token': web_token},
                json={'event': 'alston-rt.ui.dig', 'message': {'dig_id': dig_id, **settings}},
                timeout=(3, 30), allow_redirects=False)
            body = upstream.json()
            if (not isinstance(body, dict) or body.get('status') not in ('success', 'error')
                    or not isinstance(body.get('message'), str)):
                raise ValueError('Invalid configuration response')
            return jsonify(body), upstream.status_code
        except (requests.exceptions.RequestException, ValueError):
            return jsonify(status='error', code='outcome_unknown',
                message='No valid response received. Outcome unknown; check the digitiser before retrying.'), 502

    @app.before_request
    def local_only():
        if request.host.split(":")[0] not in ("127.0.0.1", "localhost"):
            abort(403, "This service only accepts localhost requests")
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            if request.headers.get("Origin") and request.headers["Origin"] != request.host_url.rstrip("/"):
                abort(403, "Cross-origin requests are not accepted")
            if request.endpoint and request.endpoint.startswith('security.'):
                return
            if request.method == 'DELETE' and not request.content_length:
                return
            if not request.is_json:
                abort(415, "Use application/json")
            if not isinstance(request.get_json(silent=True), dict):
                raise ValueError("Send a JSON object with the requested observation or job")

    @app.errorhandler(ValueError)
    @app.errorhandler(TypeError)
    def invalid(error):
        if request.path.startswith('/api/web/'):
            return jsonify(status='error', message=str(error), code='invalid_request'), 400
        return jsonify(error=str(error)), 400

    @app.errorhandler(HTTPException)
    def http_error(error):
        if request.path.startswith(("/api/web/", "/api/diagnostics/")):
            return jsonify(status='error', message=error.description, code='invalid_request'), error.code
        if request.path.startswith("/api/opt/"):
            return jsonify(error=error.description), error.code
        return error

    @app.errorhandler(OSError)
    def unavailable(error):
        return jsonify(error=str(error)), 400

    @app.route('/api/web/<path:resource>', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE'])
    def web_api(resource):
        spectrum = bool(re.fullmatch(r'observations/[A-Za-z0-9][A-Za-z0-9_.:+-]{0,254}/scans/\d+/\d+/\d+/(?:spr|signal)', resource))
        allowed = ((resource == 'profiles' and request.method == 'GET')
                   or (spectrum and request.method == 'GET')
                   or (resource == 'observations' and request.method == 'GET')
                   or (re.fullmatch(r'observations/[A-Za-z0-9][A-Za-z0-9_.:+-]{0,254}', resource) and request.method == 'GET')
                   or (resource == 'telescope/model' and request.method == 'GET')
                   or (resource == 'profile' and request.method in ('GET', 'PUT'))
                   or (resource in ('dishes', 'digitisers') and request.method in ('GET', 'POST'))
                   or (re.fullmatch(r'(dishes|digitisers)/[A-Za-z0-9][A-Za-z0-9_-]{0,127}', resource)
                       and request.method in ('GET', 'PATCH', 'DELETE')))
        if not allowed:
            abort(404)
        if not web_token:
            return jsonify(status='error', message='WEB authentication is not configured for this console', code='not_configured'), 503
        headers = {'X-Webhook-Token': web_token}
        for name in ('If-Match', 'X-Config-Profile'):
            if name in request.headers:
                headers[name] = request.headers[name]
        if spectrum and 'profile' in request.args:
            headers['X-Config-Profile'] = request.args['profile']
        upstream = None
        try:
            if resource == 'observations' or resource.startswith('observations/'):
                if set(request.args) - ({'folder', 'profile'} if spectrum else {'folder'}):
                    abort(400, 'Only relative observation folders are accepted')
                query = {'params': {'folder': request.args['folder']}} if 'folder' in request.args else {}
            elif resource == 'telescope/model':
                if set(request.args) - {'refresh'} or request.args.get('refresh', '0') not in ('0', '1'):
                    abort(400, 'Use refresh=1 to request a fresh snapshot')
                query = {'params': {'refresh': request.args['refresh']}} if 'refresh' in request.args else {}
            else:
                query = {}
            upstream = requests.request(request.method, web_url + '/api/' + resource,
                headers=headers, json=request.get_json() if request.method not in ('GET', 'DELETE') else None,
                timeout=(3, 30) if resource == 'telescope/model' and request.args.get('refresh') == '1' else (3, 10),
                allow_redirects=False, **({'stream': True} if spectrum else {}), **query)
            if spectrum and upstream.status_code == 200:
                content_type = 'image/png' if resource.endswith('/signal') else 'text/csv'
                if upstream.headers.get('Content-Type', '').split(';')[0] != content_type:
                    raise ValueError('WEB returned an invalid spectrum response')
                def content():
                    try:
                        yield from upstream.iter_content(chunk_size=64 * 1024)
                    finally:
                        upstream.close()
                response = Response(stream_with_context(content()), content_type=content_type)
                response.headers['Content-Disposition'] = upstream.headers.get('Content-Disposition', 'attachment; filename="scan-spr.csv"')
                response.headers['Cache-Control'] = 'no-store'
                response.call_on_close(upstream.close)
                return response
            body = upstream.json()
            if spectrum:
                upstream.close()
            if (not isinstance(body, dict) or body.get('status') not in ('success', 'error')
                    or not isinstance(body.get('message'), str)
                    or (body['status'] == 'success' and not isinstance(body.get('payload'), dict))):
                raise ValueError('Invalid WEB response')
        except requests.exceptions.RequestException:
            if spectrum and upstream is not None:
                upstream.close()
            return jsonify(status='error', message='Could not reach WEB. A submitted change may have completed; refresh before trying again.', code='web_unavailable'), 502
        except ValueError:
            if spectrum and upstream is not None:
                upstream.close()
            return jsonify(status='error', message='WEB returned an invalid response. Refresh to check any submitted save.', code='invalid_response'), 502
        response = jsonify(body)
        response.status_code = upstream.status_code
        response.headers['Cache-Control'] = 'no-store'
        if upstream.headers.get('ETag'):
            response.headers['ETag'] = upstream.headers['ETag']
        if upstream.headers.get('Location', '').startswith(('/api/dishes/', '/api/digitisers/')):
            response.headers['Location'] = '/api/web/' + upstream.headers['Location'][5:]
        return response

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
        return jsonify(roots=[str(p) for p in roots], directories=folders, profiles=profiles,
                       available_steps=available_pipeline_steps(), pipeline_overview=PIPELINE_OVERVIEW,
                       pipelines={profile: editable_pipeline(pipeline_snapshot(SRC / "config" / profile)) for profile in profiles})

    @app.post("/api/opt/pipeline")
    def save_pipeline():
        data = request.get_json()
        if not isinstance(data, dict):
            raise ValueError("Supply a profile and pipeline configuration")
        profile = data.get("profile")
        if not isinstance(profile, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", profile):
            raise ValueError("Choose an available configuration profile")
        profile_path = SRC / "config" / profile
        destination = profile_path / "PipelineConfig.json"
        if not destination.is_file() or destination.resolve().parent != profile_path.absolute():
            raise ValueError("Choose an available configuration profile")
        if not isinstance(data.get("pipeline"), dict):
            raise ValueError("Supply the pipeline configuration to save")
        snapshot = pipeline_snapshot(profile_path, data["pipeline"])
        content = json.dumps(snapshot, indent=4, allow_nan=False) + "\n"
        # Keep numeric range pairs compact and readable.
        content = re.sub(r"\[\s*([-+\d.eE]+),\s*([-+\d.eE]+)\s*\]", r"[\1,\2]", content)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=profile_path,
                                             prefix=".PipelineConfig-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
            temporary.chmod(destination.stat().st_mode & 0o777)
            temporary.replace(destination)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return jsonify(profile=profile, pipeline=editable_pipeline(snapshot),
                       message=f"Pipeline configuration saved to {profile}/PipelineConfig.json")

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

    @app.get("/api/opt/observations/<observation_id>/scans/<int:target>/<int:frequency>/<int:iteration>/<product>")
    def saved_scan_file(observation_id, target, frequency, iteration, product):
        from obs.catalogue import scan_spectrum_file
        folder = directory(request.args.get("directory"))
        result = scan_spectrum_file(str(folder), observation_id, target, frequency, iteration, ".", product=product)
        if result.status != "success":
            return jsonify(error=result.message, message=result.message), {
                "invalid_request": 400, "not_found": 404, "scan_incomplete": 409}.get(result.code, 503)
        response = send_file(result.payload["path"], mimetype="image/png" if product == "signal" else "text/csv",
                             as_attachment=True, download_name=result.payload["filename"], conditional=False)
        response.headers["Cache-Control"] = "no-store"
        return response

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
        pipeline = pipeline_snapshot(SRC / "config" / profile, data.get("pipeline"))
        try:
            payload = jobs.start({"directory": str(folder), "observation": obs_id, "profile": profile,
                                  "excluded": excluded, "settings": settings, "pipeline_config": pipeline})
        except RuntimeError as exc:
            return jsonify(error=str(exc)), 409
        return jsonify(payload), 202

    @app.post("/api/opt/pointing")
    def pointing_product():
        data = request.get_json()
        source = get_job(data.get("job"))
        if jobs.status(source)["state"] != "ready":
            raise ValueError("Wait for observation processing to complete")
        from ui.opt_pointing import CACHE_FILES
        source_path = source["folder"] / "out"
        if not all((source_path / name).is_file() for name in CACHE_FILES):
            raise ValueError("Reprocess this observation once to cache per-scan spectra and pointing references")
        result = json.loads((source_path / "result.json").read_text())
        aggregate_id = data.get("aggregate_id")
        if not any(row["id"] == aggregate_id for row in result["aggregates"]):
            raise ValueError("Choose a completed aggregate to view its pointings")
        animation = data.get("animation", False)
        if not isinstance(animation, bool):
            raise ValueError("Animation must be true or false")
        interval_ms = data.get("interval_ms", result.get("animation_default_interval_ms", 100))
        if type(interval_ms) is not int or not 100 <= interval_ms <= 500:
            raise ValueError("Frame interval must be a whole number between 100 and 500 ms")
        configuration = json.loads((source["folder"] / "request.json").read_text())
        configuration.pop("lab_comparison", None)
        configuration.pop("terminal_velocity", None)
        configuration.pop("aggregate_product", None)
        configuration["source_results"] = str(source_path)
        configuration["pointing_product"] = {"aggregate_id": aggregate_id, "animation": animation, "interval_ms": interval_ms}
        try:
            return jsonify(jobs.start(configuration)), 202
        except RuntimeError as exc:
            return jsonify(error=str(exc)), 409

    @app.post("/api/opt/reanalyse")
    def reanalyse():
        data = request.get_json()
        source = get_job(data.get("job"))
        if jobs.status(source)["state"] != "ready" or not (source["folder"] / "out/native-spectra.npz").exists():
            raise ValueError("Process the observation first to cache its spectra")
        configuration = json.loads((source["folder"] / "request.json").read_text())
        configuration.pop("pointing_product", None)
        configuration.pop("aggregate_product", None)
        configuration["settings"] = validate_settings(data.get("settings", {}))
        configuration.pop("terminal_velocity", None)
        terminal = data.get("terminal_velocity")
        if terminal is not None:
            from obs.terminal_velocity import TERMINAL_METHODS
            if not isinstance(terminal, dict) or not isinstance(terminal.get("method"), str) or terminal["method"] not in TERMINAL_METHODS:
                raise ValueError("Choose a supported terminal velocity method")
            result = json.loads((source["folder"] / "out/result.json").read_text())
            aggregate = next((row for row in result.get("aggregates", []) if row["id"] == terminal.get("aggregate_id")), None)
            if aggregate is None or aggregate.get("kind") != "SKY" or not aggregate.get("views", {}).get("velocity"):
                raise ValueError("Terminal velocity requires a SKY aggregate with a velocity spectrum")
            configuration["terminal_velocity"] = {"aggregate_id": aggregate["id"], "method": terminal["method"]}
        configuration.pop("lab_comparison", None)
        lab = data.get("lab_comparison")
        if lab is not None:
            if not isinstance(lab, dict):
                raise ValueError("Choose a SKY aggregate for LAB comparison")
            aggregate_id = lab.get("aggregate_id")
            result = json.loads((source["folder"] / "out/result.json").read_text())
            aggregate = next((row for row in result.get("aggregates", []) if row.get("id") == aggregate_id), None)
            if aggregate is None or aggregate.get("kind") != "SKY":
                raise ValueError("LAB comparison requires a completed SKY aggregate")
            beam = float(lab.get("beam", 5))
            if not math.isfinite(beam) or not 0 < beam <= 60:
                raise ValueError("LAB beam FWHM must be greater than 0 and at most 60 degrees")
            configuration["lab_comparison"] = {"aggregate_id": aggregate_id, "beam": beam}
            sweep = validate_lab_sweep(lab.get("sweep"))
            if sweep is not None:
                configuration["lab_comparison"]["sweep"] = sweep
        selection_only = data.get("selection_only", False)
        if not isinstance(selection_only, bool):
            raise ValueError("Selection-only update must be true or false")
        if selection_only:
            selections = [configuration[field] for field in ('lab_comparison', 'terminal_velocity') if field in configuration]
            if not selections or len({selection['aggregate_id'] for selection in selections}) != 1:
                raise ValueError("Choose options for one aggregate")
            result = json.loads((source["folder"] / "out/result.json").read_text())
            previous = validate_settings(result.get('settings', {}))
            row = next(row for row in result['aggregates'] if row['id'] == selections[0]['aggregate_id'])
            if not row.get('views', {}).get('velocity'):
                raise ValueError("Selected aggregate has no velocity spectrum")
            if any(configuration['settings'][key] != previous[key] for key in previous if key != 'total_power_basis'):
                raise ValueError("Update shared analysis settings before updating one aggregate")
            configuration['settings'] = result.get('settings', previous)
            configuration['aggregate_product'] = {'aggregate_id': selections[0]['aggregate_id']}
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
    parser.add_argument('--web-url', default=os.getenv('WEB_API_URL', 'http://127.0.0.1:8000'), help='WEB application address; authentication uses WEBHOOK_SECRET')
    parser.add_argument('--state-dir', help='Shared UI authentication state directory')
    args = parser.parse_args()
    app = create_app(args.data_root, args.cache_dir, web_url=args.web_url)
    from web.security import init_security
    init_security(app, args.state_dir)
    print(f"OPT workspace: http://127.0.0.1:{args.port}/")
    print(f"Result sessions: {app.extensions['opt_jobs'].cache}")
    try:
        app.run(host="127.0.0.1", port=args.port, debug=False, threaded=True)
    finally:
        app.extensions["opt_jobs"].close()


if __name__ == "__main__":
    main()
