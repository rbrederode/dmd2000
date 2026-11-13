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
    assert data["settings"]["baseline"] == "none"
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


@pytest.mark.parametrize("method", ["constant", "flatten"])
def test_reanalysis_uses_only_server_owned_cached_results(workspace, method):
    app, client, root, scans = workspace
    first = client.post("/api/opt/process", json=configuration(root)).json["id"]
    source = ready_job(app, first)
    response = client.post("/api/opt/reanalyse", json={"job": first, "settings": {"baseline": method},
                                                      "source_results": "/untrusted/path"})
    assert response.status_code == 202
    job = app.extensions["opt_jobs"].jobs[response.json["id"]]
    cached_configuration = json.loads((job["folder"] / "request.json").read_text())
    assert cached_configuration["source_results"] == str(source["folder"] / "out")
    assert cached_configuration["settings"]["baseline"] == method
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


def test_analysis_settings_defaults_and_one_sided_baseline_validation():
    defaults = service.validate_settings({})
    assert defaults['baseline'] == 'none'
    assert (defaults['baseline_vmin'], defaults['baseline_vmax']) == (None, None)
    with pytest.raises(ValueError, match='minimum < 0 < maximum'):
        service.validate_settings(dict(baseline='linear', baseline_vmin=10, baseline_vmax=200))
    assert service.validate_settings(dict(baseline='none', baseline_vmin=10, baseline_vmax=200))['baseline'] == 'none'
    assert service.validate_settings(dict(baseline='flatten', baseline_vmin='', baseline_vmax=None))['baseline_vmin'] is None
    assert service.validate_settings(dict(baseline_vmin=-200))['baseline_vmax'] is None


def test_plot_metric_policy_uses_enabled_digitiser_mpr_qa_settings():
    from ui.opt_pipeline import qa_metric_settings
    snapshot = {"steps_map": {
        "default": [{"step": "QA", "params": {"context": "mpr", "window_frac": .3, "smooth_window": 7}}],
        "dig003": [
            {"step": "QA", "params": {"context": "cal", "smooth_window": 1}},
            {"step": {"value": "QA"}, "params": {"context": "mpr", "window_frac": .4, "smooth_window": 5}},
            {"step": "QA", "params": {"context": "mpr", "enabled": False, "noise_estimator": "mean_rms"}},
        ],
    }}
    assert qa_metric_settings(snapshot, "dig003") == dict(noise_estimator="median_mad", window_frac=.4, smooth_window=5)
    assert qa_metric_settings(snapshot, "other") == dict(noise_estimator="median_mad", window_frac=.3, smooth_window=7)


def test_lab_comparison_request_is_scoped_to_cached_sky_aggregate(workspace):
    app, client, root, scans = workspace
    first = client.post("/api/opt/process", json=configuration(root)).json["id"]
    source = ready_job(app, first)
    service.atomic_json(source["folder"] / "out/result.json", dict(files=[], aggregates=[
        dict(id="sky", kind="SKY"), dict(id="load", kind="LOAD")]))
    for selection in [dict(aggregate_id="load"), dict(aggregate_id="missing"),
                      dict(aggregate_id="sky", beam=-1), dict(aggregate_id="sky", beam=100),
                      dict(aggregate_id="sky", beam="nan")]:
        response = client.post("/api/opt/reanalyse", json=dict(job=first, lab_comparison=selection))
        assert response.status_code == 400
    response = client.post("/api/opt/reanalyse", json=dict(job=first,
                            lab_comparison=dict(aggregate_id="sky", beam=8, fits_file="/untrusted/cube",
                                                sweep=dict(longitude=[-5, 5, .5], latitude=[-5, 5, .5]))))
    assert response.status_code == 202
    request = json.loads((app.extensions["opt_jobs"].jobs[response.json["id"]]["folder"] / "request.json").read_text())
    assert request["lab_comparison"] == dict(aggregate_id="sky", beam=8,
                                               sweep=dict(longitude=[-5, 5, .5], latitude=[-5, 5, .5]))
    assert request["source_results"] == str(source["folder"] / "out")


@pytest.mark.parametrize('sweep', [
    {}, {'longitude': [-5, 5, 0], 'latitude': [-5, 5, 1]},
    {'longitude': [5, -5, 1], 'latitude': [-5, 5, 1]},
    {'longitude': [-5, 5, .01], 'latitude': [-5, 5, .01]},
    {'longitude': [-5, float('nan'), 1], 'latitude': [-5, 5, 1]},
    {'longitude': [-181, 5, 1], 'latitude': [-5, 5, 1]},
])
def test_invalid_lab_sweep_rejected(sweep):
    with pytest.raises(ValueError):
        service.validate_lab_sweep(sweep)


def test_lab_sweep_accepts_extended_ten_degree_grid():
    sweep = dict(longitude=[-10, 10, .5], latitude=[-10, 10, .5])
    assert service.validate_lab_sweep(sweep) == sweep


@pytest.mark.parametrize('values', [dict(frequency_min_mhz=-1), dict(frequency_max_mhz=float('nan')),
                                   dict(frequency_min_mhz=1421, frequency_max_mhz=1420)])
def test_invalid_frequency_limits_rejected(values):
    with pytest.raises(ValueError):
        service.validate_settings(values)


def test_saved_observation_details_and_scan_products_use_selected_results_directory(workspace):
    app, client, root, scans = workspace
    metadata_path = root / 'example-obs.json'
    metadata = json.loads(metadata_path.read_text())
    scan = metadata['target_scans'][0]['scans'][0]
    scan['files_prefix'] = 'recorded-scan'
    scan['files_directory'] = '/previous/machine'
    metadata_path.write_text(json.dumps(metadata))
    png = b'\x89PNG\r\n\x1a\nimage'
    (root / 'recorded-scan-sigfig.png').write_bytes(png)
    (root / 'recorded-scan-spr.csv').write_bytes(b'frequency,power\n1420,1\n')
    summary = client.post('/api/opt/open', json=configuration(root)).json
    assert summary['metadata']['_type'] == 'ObsModel'
    assert summary['metadata']['target_scans'][0]['scans'][0]['files_prefix'] == 'recorded-scan'
    entry = client.get('/api/opt/observations', query_string=dict(directory=str(root))).json['entries'][0]
    assert entry['completed'] is True
    for key in ['obs_state', 'scheduling_block_start', 'scheduling_block_end', 'created', 'user_email']:
        assert key in entry
    base = '/api/opt/observations/example/scans/1/0/0/'
    response = client.get(base + 'signal', query_string=dict(directory=str(root)))
    assert response.status_code == 200 and response.data == png
    assert response.mimetype == 'image/png'
    assert 'recorded-scan-sigfig.png' in response.headers['Content-Disposition']
    response.close()
    response = client.get(base + 'spr', query_string=dict(directory=str(root)))
    assert response.status_code == 200 and response.mimetype == 'text/csv'
    response.close()
    assert client.get(base + 'signal', query_string=dict(directory=str(root.parent))).status_code == 400
    scan['status']['value'] = 'WIP'; metadata_path.write_text(json.dumps(metadata))
    assert client.get(base + 'signal', query_string=dict(directory=str(root))).status_code == 409
    scan['status']['value'] = 'COMPLETE'; metadata_path.write_text(json.dumps(metadata))
    (root / 'recorded-scan-sigfig.png').unlink()
    assert client.get(base + 'signal', query_string=dict(directory=str(root))).status_code == 404
    outside = root.parent / 'outside.png'; outside.write_bytes(png)
    (root / 'recorded-scan-sigfig.png').symlink_to(outside)
    assert client.get(base + 'signal', query_string=dict(directory=str(root))).status_code == 404


def test_saved_filename_is_preserved_when_observation_id_differs(workspace):
    app, client, root, scans = workspace
    (root / 'archived-copy-obs.json').write_bytes((root / 'example-obs.json').read_bytes())
    response = client.post('/api/opt/open', json=configuration(root, observation='archived-copy'))
    assert response.status_code == 200
    assert response.json['id'] == 'example'
    assert response.json['file_id'] == 'archived-copy'
    response = client.post('/api/opt/process', json=configuration(root, observation='archived-copy'))
    assert response.status_code == 202
    job = app.extensions['opt_jobs'].jobs[response.json['id']]
    assert json.loads((job['folder'] / 'request.json').read_text())['observation'] == 'archived-copy'


@pytest.mark.parametrize("method", ["constant", "flatten"])
def test_run_pipeline_snapshot_does_not_change_profile(workspace, method):
    app, client, root, scans = workspace
    profile = service.SRC / "config/default/PipelineConfig.json"
    original = profile.read_bytes()
    pipeline = {"default": [{"step": "BASELINE", "params": {"context": "cal", "method": method}}]}
    response = client.post("/api/opt/process", json=configuration(root, pipeline=pipeline))
    assert response.status_code == 202
    job = app.extensions["opt_jobs"].jobs[response.json["id"]]
    snapshot = json.loads((job["folder"] / "request.json").read_text())["pipeline_config"]
    assert snapshot["steps_map"]["default"][0]["params"]["method"] == method
    assert profile.read_bytes() == original


@pytest.mark.parametrize("pipeline", [
    {"default": [{"step": "os.system", "params": {"context": "cal"}}]},
    {"default": [{"step": "BASELINE", "params": {"context": "mpr"}}]},
    {"default": [{"step": "BASELINE", "params": {"context": "cal", "method": "bad"}}]},
    {"default": [{"step": "BASELINE", "params": {"context": "cal", "scan": {}}}]},
])
def test_invalid_pipeline_overrides_fail_before_worker_launch(workspace, pipeline):
    app, client, root, scans = workspace
    response = client.post("/api/opt/process", json=configuration(root, pipeline=pipeline))
    assert response.status_code == 400
    assert not app.extensions["opt_jobs"].jobs



def test_available_pipeline_steps_are_discovered_without_altering_profile(workspace):
    app, client, root, scans = workspace
    response = client.get("/api/opt/config")
    assert response.status_code == 200
    overview = response.json["pipeline_overview"]
    assert [stage["title"].split(" — ")[0] for stage in overview["stages"]] == ["1. spr", "2. cal", "3. mpr"]
    assert "does not replay spr steps" in " ".join(overview["notes"])
    steps = {entry["step"]: entry for entry in response.json["available_steps"]}
    assert set(steps) == {"DC_SPIKE", "LOAD", "BANDPASS_FILTER", "RFI_FLAG", "BASELINE", "QA"}
    assert steps["BASELINE"]["params"]["method"] == "flatten"
    assert steps["BASELINE"]["params"]["context"] == "cal"
    assert steps["BANDPASS_FILTER"]["params"]["ranges_pct"] == [[0, 100]]
    assert all(entry["description"] for entry in steps.values())
    for entry in steps.values():
        assert entry["help"]["overview"]
        assert entry["help"]["notes"]
        assert set(entry["params"]) | {"enabled"} <= set(entry["help"]["parameters"])
    assert "not rescaled" in steps["RFI_FLAG"]["help"]["parameters"]["threshold"]
    assert "usable channels" in steps["BASELINE"]["help"]["parameters"]["ranges_pct"]


def test_total_power_basis_validation():
    for basis in ("frequency", "velocity"):
        assert service.validate_settings({"total_power_basis": basis})["total_power_basis"] == basis
    with pytest.raises(ValueError, match="Total Power baseline source"):
        service.validate_settings({"total_power_basis": "other"})


def test_save_pipeline_validates_and_persists_only_selected_profile(workspace, tmp_path, monkeypatch):
    import shutil
    _, client, _, _ = workspace
    original = service.SRC/'config/default/PipelineConfig.json'
    config_root = tmp_path/'source'
    for profile in ('default', 'rotation'):
        folder = config_root/'config'/profile
        folder.mkdir(parents=True)
        shutil.copy2(original, folder/'PipelineConfig.json')
    monkeypatch.setattr(service, 'SRC', config_root)
    untouched = (config_root/'config/default/PipelineConfig.json').read_bytes()
    pipeline = {'default': [{'step':'BASELINE', 'params': {'context':'cal', 'method':'flatten',
                                                          'ranges_pct':[[0,10],[90,100]]}}]}
    response = client.post('/api/opt/pipeline', json={'profile':'rotation', 'pipeline':pipeline})
    assert response.status_code == 200
    assert response.json['pipeline'] == pipeline
    saved = config_root/'config/rotation/PipelineConfig.json'
    content = saved.read_text()
    assert '[0,10]' in content and '[90,100]' in content
    from models.pipeline import PipelineConfig
    from ui.opt_pipeline import editable_pipeline
    assert editable_pipeline(PipelineConfig.load_from_disk(str(saved.parent), saved.name).to_dict()) == pipeline
    assert (config_root/'config/default/PipelineConfig.json').read_bytes() == untouched
    before = saved.read_bytes()
    for body in ({'profile':'../rotation','pipeline':pipeline}, {'profile':'missing','pipeline':pipeline},
                 {'profile':'rotation'}, {'profile':'rotation','pipeline':{}},
                 {'profile':'rotation','pipeline':{'default':[{'step':'UNKNOWN','params':{}}]}}):
        assert client.post('/api/opt/pipeline', json=body).status_code == 400
        assert saved.read_bytes() == before
    assert not list(saved.parent.glob('.PipelineConfig-*.tmp'))


@pytest.mark.parametrize("interval", [None, 300])
def test_pointing_endpoint_starts_isolated_cache_job_and_reanalysis_clears_action(workspace, interval):
    app,client,root,_=workspace
    job_id=client.post('/api/opt/process',json=configuration(root)).json['id']
    job=ready_job(app,job_id)
    out=job['folder']/'out'
    service.atomic_json(out/'result.json',dict(aggregates=[dict(id='sky',kind='SKY')],files=[],views={},pointing_cache=True))
    assert client.post('/api/opt/pointing',json={'job':job_id,'aggregate_id':'sky'}).status_code==400
    (out/'native-pointing.json').write_text('{}')
    (out/'native-pointing.npz').write_bytes(b'cache')
    for body in ({'aggregate_id':'missing'},{'aggregate_id':'sky','animation':'yes'},
                 *({'aggregate_id':'sky','animation':True,'interval_ms':value} for value in (99,501,True,'200',100.5))):
        assert client.post('/api/opt/pointing',json={'job':job_id,**body}).status_code==400
    response=client.post('/api/opt/pointing',json={'job':job_id,'aggregate_id':'sky','animation':True,
        **({'interval_ms':interval} if interval is not None else {})})
    assert response.status_code==202
    next_job=app.extensions['opt_jobs'].jobs[response.json['id']]
    request=json.loads((next_job['folder']/'request.json').read_text())
    assert request['source_results']==str(out)
    assert request['pointing_product']=={'aggregate_id':'sky','animation':True,'interval_ms':interval or 100}
    ready_job(app,response.json['id'])
    updated=client.post('/api/opt/reanalyse',json={'job':response.json['id'],'settings':{'baseline':'none'}})
    assert updated.status_code==202
    update_job=app.extensions['opt_jobs'].jobs[updated.json['id']]
    update_request=json.loads((update_job['folder']/'request.json').read_text())
    assert 'pointing_product' not in update_request
    update_job['process'].code=0


@pytest.mark.parametrize('method',['threshold','connected','erfc'])
def test_terminal_velocity_selection_is_validated_and_not_inherited(workspace,method):
    app,client,root,_=workspace
    job_id=client.post('/api/opt/process',json=configuration(root)).json['id']
    job=ready_job(app,job_id)
    service.atomic_json(job['folder']/'out/result.json',dict(aggregates=[
        dict(id='sky',kind='SKY',views={'velocity':'sky.png'}),dict(id='load',kind='LOAD',views={}),
        dict(id='no-velocity',kind='SKY',views={})],files=[]))
    for selection in (False,{},dict(method=[]),dict(method='unknown',aggregate_id='sky'),
                      dict(method=method,aggregate_id='load'),dict(method=method,aggregate_id='missing'),
                      dict(method=method,aggregate_id='no-velocity')):
        assert client.post('/api/opt/reanalyse',json={'job':job_id,'terminal_velocity':selection}).status_code == 400
    selection=dict(method=method,aggregate_id='sky')
    response=client.post('/api/opt/reanalyse',json={'job':job_id,'terminal_velocity':selection})
    assert response.status_code == 202
    followup=app.extensions['opt_jobs'].jobs[response.json['id']]
    assert json.loads((followup['folder']/'request.json').read_text())['terminal_velocity'] == selection
    ready_job(app,followup['id'])
    response=client.post('/api/opt/reanalyse',json={'job':followup['id']})
    assert response.status_code == 202
    request=app.extensions['opt_jobs'].jobs[response.json['id']]['folder']/'request.json'
    assert 'terminal_velocity' not in json.loads(request.read_text())


def test_selection_only_update_routes_one_aggregate_and_rejects_shared_changes(workspace):
    app,client,root,_=workspace
    job_id=client.post('/api/opt/process',json=configuration(root)).json['id']
    job=ready_job(app,job_id)
    service.atomic_json(job['folder']/'out/result.json',dict(settings=service.validate_settings({}),aggregates=[
        dict(id='sky',kind='SKY',views={'velocity':'sky.png'}),dict(id='other',kind='SKY',views={'velocity':'other.png'})],files=[]))
    selection=dict(aggregate_id='sky',method='threshold')
    for body in (dict(selection_only='yes'),dict(selection_only=True),
                 dict(selection_only=True,terminal_velocity=selection,settings={'baseline':'linear'}),
                 dict(selection_only=True,terminal_velocity=selection,lab_comparison={'aggregate_id':'other','beam':5})):
        assert client.post('/api/opt/reanalyse',json={'job':job_id,**body}).status_code == 400
    response=client.post('/api/opt/reanalyse',json={'job':job_id,'selection_only':True,'terminal_velocity':selection})
    assert response.status_code == 202
    next_job=app.extensions['opt_jobs'].jobs[response.json['id']]
    request=json.loads((next_job['folder']/'request.json').read_text())
    assert request['aggregate_product'] == {'aggregate_id':'sky'}
    ready_job(app,next_job['id'])
    response=client.post('/api/opt/reanalyse',json={'job':next_job['id']})
    followup=app.extensions['opt_jobs'].jobs[response.json['id']]
    assert 'aggregate_product' not in json.loads((followup['folder']/'request.json').read_text())
