import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from astropy.coordinates import SkyCoord
from astropy import units as u

from models.obs import ObsModel
from models.scan import ScanModel, ScanState, ScanType
from models.target import TargetModel, TargetScanSet
from ui import opt_service as service


class FakeProcess:
    def __init__(self, command, **options):
        self.command = command
        self.options = options
        self.code = None
    def poll(self): return self.code
    def terminate(self): self.code = -15
    def kill(self): self.code = -9
    def wait(self, timeout=None): return self.code


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "observations"
    root.mkdir()
    scans = [ScanModel(obs_id="example", tgt_idx=1, freq_scan=0, scan_iter=i,
                       dig_id="dig003", scan_type=ScanType.SKY, status=ScanState.COMPLETE,
                       duration=60, center_freq=1420e6, spectral_resolution=16, sample_rate=1e6)
             for i in range(2)]
    obs = ObsModel(obs_id="example", targets=[TargetModel(tgt_idx=0), TargetModel(tgt_idx=1,
                    sky_coord=SkyCoord(l=30*u.deg, b=0*u.deg, frame="galactic"))],
                   target_scans=[TargetScanSet(tgt_idx=1, scans=scans)])
    obs.save_to_disk(output_dir=str(root))
    monkeypatch.setattr(service.subprocess, "Popen", FakeProcess)
    app = service.create_app([root], tmp_path / "cache")
    app.testing = True
    yield app, app.test_client(), root, scans
    app.extensions["opt_jobs"].close()


def configuration(root, **kwargs):
    return {"directory": str(root), "observation": "example", "profile": "default", "excluded": [], **kwargs}


def ready_job(app, job_id):
    job = app.extensions["opt_jobs"].jobs[job_id]
    out = job["folder"] / "out"
    out.mkdir()
    service.atomic_json(out / "result.json", {"files": ["plot.png"], "aggregates": []})
    (out / "plot.png").write_bytes(b"image")
    (out / "native-spectra.npz").write_bytes(b"native cache")
    job["process"].code = 0
    return job


def test_open_and_scan_review_are_read_only(workspace):
    app, client, root, scans = workspace
    before = (root / "example-obs.json").read_bytes()
    found = client.get("/api/opt/observations", query_string={"directory": str(root)})
    assert found.json["observations"] == ["example"]
    response = client.post("/api/opt/open", json=configuration(root))
    assert response.status_code == 200
    assert len(response.json["scans"]) == 2
    assert response.json["scans"][0]["longitude"] == 30
    assert response.json["scans"][0]["status"] == "COMPLETE"
    assert (root / "example-obs.json").read_bytes() == before
    page = client.get("/")
    assert b"Observation Processing" in page.data
    assert b"opt.js" in page.data
    assert client.get("/opt.js").status_code == 200


def test_background_processing_uses_validated_snapshot_and_no_stdin(workspace):
    app, client, root, scans = workspace
    response = client.post("/api/opt/process", json=configuration(root, excluded=[scans[0].scan_id]))
    assert response.status_code == 202
    job = app.extensions["opt_jobs"].jobs[response.json["id"]]
    data = json.loads((job["folder"] / "request.json").read_text())
    assert data["excluded"] == [scans[0].scan_id]
    assert data["settings"]["baseline"] == "linear"
    assert job["process"].options["stdin"] == service.subprocess.DEVNULL
    assert job["process"].options["env"]["MPLBACKEND"] == "Agg"
    assert client.post("/api/opt/process", json=configuration(root)).status_code == 409
    cancelled = client.post(f"/api/opt/jobs/{job['id']}/cancel", json={})
    assert cancelled.json["state"] == "cancelled"
    assert client.get(f"/api/opt/jobs/{job['id']}/files/plot.png").status_code == 404


def test_completed_files_are_scoped_to_the_manifest(workspace):
    app, client, root, scans = workspace
    job_id = client.post("/api/opt/process", json=configuration(root)).json["id"]
    job = ready_job(app, job_id)
    (job["folder"] / "out/private.json").write_text("private")
    assert client.get(f"/api/opt/jobs/{job_id}").json["state"] == "ready"
    assert client.get(f"/api/opt/jobs/{job_id}/files/plot.png").data == b"image"
    download = client.get(f"/api/opt/jobs/{job_id}/files/plot.png?download=1")
    assert "attachment" in download.headers["Content-Disposition"]
    assert client.get(f"/api/opt/jobs/{job_id}/files/private.json").status_code == 404
    assert client.get("/api/opt/jobs/unknown").status_code == 404


def test_reanalysis_uses_only_server_owned_cached_results(workspace):
    app, client, root, scans = workspace
    first = client.post("/api/opt/process", json=configuration(root)).json["id"]
    source = ready_job(app, first)
    response = client.post("/api/opt/reanalyse", json={"job": first, "settings": {"baseline": "constant"},
                                                      "source_results": "/untrusted/path"})
    assert response.status_code == 202
    job = app.extensions["opt_jobs"].jobs[response.json["id"]]
    cached_configuration = json.loads((job["folder"] / "request.json").read_text())
    assert cached_configuration["source_results"] == str(source["folder"] / "out")
    assert cached_configuration["settings"]["baseline"] == "constant"
    assert cached_configuration["excluded"] == []


@pytest.mark.parametrize("changes", [
    {"observation": "../example"}, {"profile": "../../escape"},
    {"excluded": ["unknown"]}, {"settings": {"baseline": "bad"}},
    {"settings": {"baseline_vmin": 200, "baseline_vmax": 100}},
    {"settings": {"contours": [2, 1]}}, {"settings": None},
])
def test_invalid_jobs_fail_before_launch(workspace, changes):
    app, client, root, scans = workspace
    response = client.post("/api/opt/process", json=configuration(root, **changes))
    assert response.status_code == 400
    assert not app.extensions["opt_jobs"].jobs


def test_excluding_all_sky_scans_is_reported(workspace):
    app, client, root, scans = workspace
    response = client.post("/api/opt/process", json=configuration(root, excluded=[s.scan_id for s in scans]))
    assert response.status_code == 400
    assert "completed SKY" in response.json["error"]


def test_data_roots_symlinks_host_and_origin_are_enforced(workspace, tmp_path):
    app, client, root, scans = workspace
    assert client.get("/api/opt/observations", query_string={"directory": str(tmp_path)}).status_code == 400
    (root / "escape").symlink_to(tmp_path, target_is_directory=True)
    assert client.get("/api/opt/observations", query_string={"directory": str(root / "escape")}).status_code == 400
    assert client.get("/api/opt/config", headers={"Host": "evil.example"}).status_code == 403
    assert client.post("/api/opt/open", json=configuration(root), headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/opt/open", json=[]).status_code == 400
    assert not app.extensions["opt_jobs"].jobs


def test_observation_catalogue_counts_only_completed_scans_and_time(workspace):
    app, client, root, scans = workspace
    model = ObsModel.load_from_disk(input_dir=str(root), filename="example-obs.json")
    model.title = "Galactic plane rotation"
    model.dsh_id = "dish003"
    model.total_integration_time = 999999  # Planned total must not be used.
    model.target_scans[0].scans.extend([
        ScanModel(obs_id="example", tgt_idx=1, freq_scan=0, scan_iter=2,
                  dig_id="dig003", scan_type=ScanType.LOAD, status=ScanState.COMPLETE, duration=15),
        ScanModel(obs_id="example", tgt_idx=1, freq_scan=0, scan_iter=3,
                  dig_id="dig003", scan_type=ScanType.SKY, status=ScanState.WIP, duration=600),
    ])
    model.save_to_disk(output_dir=str(root))
    before = (root / "example-obs.json").read_bytes()
    response = client.get("/api/opt/observations", query_string={"directory": str(root)})
    assert response.status_code == 200
    entry = response.json["entries"][0]
    assert entry["id"] == "example"
    assert entry["name"] == "Galactic plane rotation"
    assert entry["dish"] == "dish003"
    assert entry["completed_scans"] == 3 and entry["total_scans"] == 4
    assert entry["completed_types"] == {"SKY": 2, "LOAD": 1}
    assert entry["completed_integration_s"] == 135
    assert (root / "example-obs.json").read_bytes() == before


def test_bad_observation_metadata_does_not_hide_other_listings(workspace):
    app, client, root, scans = workspace
    (root / "broken-obs.json").write_text("invalid json")
    response = client.get("/api/opt/observations", query_string={"directory": str(root)})
    assert response.status_code == 200
    entries = response.json["entries"]
    assert entries[0]["id"] == "example" and not entries[0]["error"]
    assert entries[1]["id"] == "broken" and entries[1]["error"]


def test_health_endpoint_is_available_without_loading_observations(workspace, monkeypatch):
    app, client, root, scans = workspace
    monkeypatch.setattr(service.ObsModel, "load_from_disk", lambda *a, **k: pytest.fail("Health must not read observations"))
    response = client.get("/api/opt/health")
    assert response.status_code == 200
    assert response.json == {"status": "ok"}
