"""Distribution uses the plot's measurements, not saved QA or a new estimator."""
import csv
import json
from pathlib import Path
from types import SimpleNamespace

import matplotlib
matplotlib.use('Agg')
from matplotlib import pyplot as plt
import numpy as np
import pytest

from obs.dist_map import mean_cells, read_snr_csv, plot_hi_snr_map, _make_cell_polygons
from obs.obs_display import save_frequency_record
from obs import spectral_annotations
from ui.opt_distribution import export_distribution, render_distribution_product, COLUMNS


def aggregate(identifier='obs-1-0', **changes):
    return dict(scan_id=identifier, kind='SKY', pointing='SIDEREAL_TRACK', gain_db=35,
                integration_s=240, centre_freq_hz=1420e6, sample_rate_hz=6e6,
                longitude=30., latitude=0.,
                velocity_metrics=dict(status='measured', peak_snr=12.),
                frequency_metrics=dict(status='measured', peak_snr=9.), **changes)


def test_minimal_csv_and_opt_csv_can_be_read_without_qa_files(tmp_path):
    path = tmp_path / 'simple.csv'
    path.write_text('long,lat,SNR\n30,0,12\n31,0,18\n')
    assert [row['snr'] for row in read_snr_csv(path)] == [12, 18]
    path.write_text('galactic_longitude_deg,galactic_latitude_deg,velocity_snr,frequency_snr,gain_db\n30,0,12,9,35\n31,0,,11,35\n')
    with pytest.raises(ValueError, match='snr columns'):
        read_snr_csv(path)  # Choosing between plot measurements belongs to the UI adapter.


def test_repeated_cells_average_linear_snr_and_wrap_longitude():
    cells = mean_cells([dict(l=359, b=0, snr=10), dict(l=1, b=1, snr=30), dict(l=10, b=0, snr=8)])
    assert cells == [dict(l=0., b=0., snr=20., count=2), dict(l=10., b=0., snr=8., count=1)]
    polygons = _make_cell_polygons(180, 0, 5, 5)
    assert len(polygons) == 2
    assert all(np.ptp(polygon.get_xy()[:, 0]) <= np.radians(5) for polygon in polygons)
    figure, axis = plot_hi_snr_map([dict(l=90, b=0, snr=10)])
    try:
        assert axis.xaxis.get_major_formatter()(-np.pi / 2, 0) == '90°'
        assert np.ptp(axis.collections[0].get_paths()[0].vertices[:, 1]) == pytest.approx(np.radians(5))
    finally:
        plt.close(figure)


def test_export_retains_acquisition_details_and_both_existing_plot_snrs(tmp_path, monkeypatch):
    # Any attempt to measure spectra while exporting would fail this test.
    monkeypatch.setattr(spectral_annotations, 'observation_noise_metrics', lambda *a, **k: pytest.fail('duplicate measurement'))
    drift = aggregate('drift'); drift['pointing'] = 'DRIFT_SCAN'
    load = aggregate('load'); load['kind'] = 'LOAD'
    result = dict(observation='example', aggregates=[aggregate(), drift, load], views={})
    export_distribution(result, tmp_path)
    with (tmp_path / 'distribution.csv').open() as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
        assert tuple(reader.fieldnames) == COLUMNS
    assert len(rows) == 1
    row = rows[0]
    assert row['scan_id'] == 'obs-1-0'
    assert float(row['gain_db']) == 35
    assert float(row['integration_time_s']) == 240
    assert float(row['centre_freq_hz']) == 1420e6
    assert float(row['sample_rate_hz']) == 6e6
    assert float(row['velocity_snr']) == 12 and float(row['frequency_snr']) == 9
    assert result['distribution']['b_step'] == 15
    assert result['distribution']['l_step'] == 15
    assert result['distribution']['cell_shape'] == 'diamond'
    assert result['distribution']['stretch'] == 'log'
    assert result['distribution']['snr_column'] == 'velocity_snr'
    assert result['distribution']['snr_max'] is None
    assert result['views']['distribution'] == 'distribution.png'
    assert (tmp_path / 'distribution.png').is_file()


def test_map_only_job_switches_snr_without_changing_export_or_reprocessing(tmp_path, monkeypatch):
    source, out = tmp_path / 'source', tmp_path / 'out'
    source.mkdir(); out.mkdir()
    result = dict(observation='example', aggregates=[aggregate()], views={})
    export_distribution(result, source)
    (source / 'result.json').write_text(json.dumps(result))
    original = (source / 'distribution.csv').read_bytes()
    monkeypatch.setattr(spectral_annotations, 'observation_noise_metrics', lambda *a, **k: pytest.fail('duplicate measurement'))
    result = render_distribution_product(dict(source_results=str(source),
        distribution_product=dict(snr_column='frequency_snr', l_step=10, b_step=5, cell_shape='diamond', stretch='asinh', snr_max=20)), out, lambda *a: None)
    assert result['distribution']['snr_column'] == 'frequency_snr'
    assert result['distribution']['colour_limits'] == [0,20]
    assert result['distribution']['stretch'] == 'asinh'
    assert (out / 'distribution.csv').read_bytes() == original
    assert (out / 'distribution.png').is_file()


def test_no_sidereal_means_no_distribution_files_or_tab_flag(tmp_path):
    row = aggregate(); row['pointing'] = 'OFFSET_SCAN'
    result = dict(observation='none', aggregates=[row], views={})
    export_distribution(result, tmp_path)
    assert 'distribution' not in result and not list(tmp_path.iterdir())


def test_unavailable_velocity_keeps_csv_and_frequency_switch_available(tmp_path):
    row = aggregate(); row['velocity_metrics'] = None
    result = dict(observation='example', aggregates=[row], views={})
    export_distribution(result, tmp_path)
    assert (tmp_path / 'distribution.csv').is_file()
    assert 'distribution' not in result['views']
    assert 'Velocity SNR map unavailable' in result['view_errors']['distribution']


def test_frequency_measurement_is_computed_once_and_returned_exactly(tmp_path, monkeypatch):
    calls = []
    measure = spectral_annotations.observation_noise_metrics
    def capture(*args, **kwargs):
        measured = measure(*args, **kwargs)
        calls.append(measured)
        return measured
    monkeypatch.setattr(spectral_annotations, 'observation_noise_metrics', capture)
    x = np.linspace(1417., 1423., 256)
    rng = np.random.default_rng(7)
    power = rng.normal(1, .01, len(x)) + .4 * np.exp(-((x-1420.4)/.15)**2)
    record = save_frequency_record(dict(target_id=1, freq_scan=0, frequency_mhz=x, power=power),
                                   tmp_path / 'frequency.png', 'example', 'none', fmin=1418, fmax=1422)
    assert len(calls) == 1
    assert record['spectral_metrics'] is calls[0]
    assert calls[0]['signal_range_kms'][0] >= -2
    assert record['spectral_metrics']['peak_snr'] > 0


def test_full_opt_worker_exports_matching_metrics_and_refreshes_them_from_cache(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    import zipfile
    from astropy.coordinates import SkyCoord
    from astropy import units as u
    from astropy.utils import iers
    from models.obs import ObsModel
    from models.target import TargetModel, TargetConfig, TargetScanSet, PointingType
    from models.scan import ScanModel, ScanState, ScanType
    from models.pipeline import PipelineConfig, StepConfig, StepType
    from obs.scan import Scan
    from ui import opt_worker
    from ui.opt_service import validate_settings
    monkeypatch.setattr(iers.conf, 'auto_download', False)
    data = tmp_path / 'data'; data.mkdir()
    targets, configs, groups = [], [], []
    start = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    rng = np.random.default_rng(37)
    for index, kind in enumerate((ScanType.LOAD, ScanType.SKY)):
        model = ScanModel(obs_id='test', tgt_idx=index, freq_scan=0, scan_iter=0, dig_id='dig001',
            duration=3, sample_rate=1e6, spectral_resolution=128, center_freq=1420400000., gain=35.,
            scan_type=kind, load=kind==ScanType.LOAD, status=ScanState.COMPLETE,
            read_start=start + timedelta(seconds=5*index), read_end=start + timedelta(seconds=5*index+3))
        scan = Scan(model)
        x = np.arange(128)
        scan.spr[:] = 100000 * (1 + rng.normal(0, .003, (3,128)))
        if kind == ScanType.SKY:
            scan.spr[:] += 50000 * np.exp(-((x-64)/5)**2)
        assert scan.save_to_disk(str(data))
        targets.append(TargetModel(obs_id='test', tgt_idx=index, pointing=PointingType.SIDEREAL_TRACK if index else PointingType.DRIFT_SCAN,
                                   sky_coord=SkyCoord(l=30*u.deg, b=0*u.deg, frame='galactic') if index else None,
                                   altaz={'alt':30.,'az':180.} if not index else None))
        configs.append(TargetConfig(obs_id='test', tgt_idx=index, gain=35., sample_rate=1e6, bandwidth=1e6,
                                    spectral_resolution=128, center_freq=1420400000., integration_time=3))
        groups.append(TargetScanSet(obs_id='test', tgt_idx=index, scans=[model], freq_scans=1, scan_iterations=1, scan_duration=3))
    obs = ObsModel(obs_id='test', targets=targets, target_configs=configs, target_scans=groups,
                   latitude=53., longitude=-2., height=88., start_dt=start, end_dt=start+timedelta(seconds=10))
    obs.save_to_disk(str(data))
    config = PipelineConfig(steps_map={'default':[
        StepConfig(step=StepType.LOAD, params={'context':'cal'}),
        StepConfig(step=StepType.QA, params={'context':'mpr','noise_estimator':'median_mad'})]})
    request = dict(directory=str(data), observation='test', profile='local', excluded=[],
                   settings=validate_settings({}), pipeline_config=config.to_dict())
    job = tmp_path / 'job'; job.mkdir()
    (job / 'request.json').write_text(json.dumps(request))
    opt_worker.run(job)
    result = json.loads((job / 'out/result.json').read_text())
    assert result['distribution']['aggregates'] == 1
    row = next(row for row in result['aggregates'] if row['kind']=='SKY')
    with (job / 'out/distribution.csv').open() as stream:
        exported = next(csv.DictReader(stream))
    assert float(exported['velocity_snr']) == pytest.approx(row['velocity_metrics']['peak_snr'])
    assert float(exported['frequency_snr']) == pytest.approx(row['frequency_metrics']['peak_snr'])
    assert float(exported['gain_db']) == 35 and float(exported['integration_time_s']) == 3
    with zipfile.ZipFile(job / 'out/results.zip') as archive:
        assert {'distribution.csv','distribution.png'} <= set(archive.namelist())
    # Plot updates replay only cached native spectra, never the physical scan processing.
    monkeypatch.setattr(opt_worker, 'process_observation', lambda *a, **k: pytest.fail('physical scan replay'))
    updated = tmp_path / 'updated'; updated.mkdir()
    request.update(source_results=str(job / 'out'), settings=validate_settings(dict(baseline='constant', baseline_vmin=-70, baseline_vmax=70)))
    (updated / 'request.json').write_text(json.dumps(request))
    opt_worker.run(updated)
    result = json.loads((updated / 'out/result.json').read_text())
    row = next(row for row in result['aggregates'] if row['kind']=='SKY')
    with (updated / 'out/distribution.csv').open() as stream:
        exported = next(csv.DictReader(stream))
    assert float(exported['velocity_snr']) == pytest.approx(row['velocity_metrics']['peak_snr'])
    assert float(exported['frequency_snr']) == pytest.approx(row['frequency_metrics']['peak_snr'])


def test_ui_adapter_passes_only_long_lat_snr_to_generic_renderer(tmp_path, monkeypatch):
    import ui.opt_distribution as module
    captured = []
    render = module.plot_hi_snr_map_from_csv
    def capture(path, **kwargs):
        with Path(path).open() as stream:
            reader = csv.DictReader(stream)
            rows = list(reader)
            assert reader.fieldnames == ['long', 'lat', 'snr']
        captured.extend(rows)
        assert 'snr_column' not in kwargs
        return render(path, **kwargs)
    monkeypatch.setattr(module, 'plot_hi_snr_map_from_csv', capture)
    result = dict(observation='example', aggregates=[aggregate()], views={},
                  distribution=dict(snr_column='frequency_snr', l_step=5, b_step=5))
    export_distribution(result, tmp_path)
    assert float(captured[0]['snr']) == 9
    assert not list(tmp_path.glob('.distribution-input-*'))
    with (tmp_path / 'distribution.csv').open() as stream:
        row = next(csv.DictReader(stream))
    assert float(row['velocity_snr']) == 12 and float(row['frequency_snr']) == 9
    assert float(row['gain_db']) == 35


def test_older_cache_requires_acquisition_details_instead_of_exporting_guesses(tmp_path):
    row = aggregate(); row.pop('gain_db')
    result = dict(observation='old', aggregates=[row], views={})
    export_distribution(result, tmp_path)
    assert 'distribution' not in result
    assert not (tmp_path / 'distribution.csv').exists()
    assert any('Reprocess' in warning for warning in result['warnings'])


def test_colour_scale_zero_anchor_and_sparse_maximum_after_averaging():
    from obs.dist_map import snr_colour_norm
    cells = mean_cells([dict(l=0, b=0, snr=10), dict(l=1, b=0, snr=30), dict(l=10, b=0, snr=12)])
    norm, policy = snr_colour_norm(cells)
    assert norm.vmin == 0 and norm.vmax == 20
    assert policy == 'auto maximum'
    assert norm(10) == pytest.approx(.5)
    # Projection seam polygon duplicates must not turn sparse samples into P99 data.
    figure, axis = plot_hi_snr_map([dict(l=180, b=0, snr=20)] * 40)
    try:
        assert axis.collections[0].norm.vmax == 20
        assert 'auto maximum' in figure.axes[-1].get_xlabel()
    finally:
        plt.close(figure)


def test_colour_scale_uses_p99_of_populated_cell_means_for_large_samples():
    from obs.dist_map import snr_colour_norm
    cells = [dict(snr=10.) for _ in range(99)] + [dict(snr=1000.)]
    norm, policy = snr_colour_norm(cells)
    assert policy == 'auto P99'
    assert norm.vmin == 0
    assert norm.vmax == pytest.approx(np.percentile([c['snr'] for c in cells], 99))
    assert norm.vmax < 1000 and norm(1000) == 1


@pytest.mark.parametrize('stretch', ['linear','asinh','log'])
def test_manual_limits_and_zero_safe_stretches_preserve_values(stretch):
    from obs.dist_map import snr_colour_norm
    cells = [dict(snr=0.), dict(snr=10.), dict(snr=50.)]
    norm, policy = snr_colour_norm(cells, stretch, 100)
    assert norm(0) == 0 and norm(100) == 1
    assert np.all(np.isfinite(norm([0,1,10,100])))
    np.testing.assert_allclose(norm.inverse(norm([0,1,10,100])), [0,1,10,100], atol=1e-10)
    assert policy == 'fixed upper limit'
    assert [c['snr'] for c in cells] == [0,10,50]
    if stretch != 'linear':
        assert norm(10) > .1
    empty_signal, _ = snr_colour_norm([dict(snr=0)], stretch)
    assert empty_signal.vmin == 0 and empty_signal.vmax > 0


def test_invalid_colour_settings_are_rejected():
    from ui.opt_distribution import validate_options
    for options in ({'snr_max':0}, {'snr_max':-1}, {'snr_max':float('nan')}, {'snr_max':float('inf')}, {'stretch':'invalid'}):
        with pytest.raises(ValueError):
            validate_options(options)


@pytest.mark.parametrize('stretch', ['linear','asinh','log'])
def test_colour_stretches_render_with_zero_and_show_clipping_indicator(tmp_path, stretch):
    cells = [dict(l=0, b=0, snr=0), dict(l=10, b=0, snr=10), dict(l=20, b=0, snr=100)]
    figure, axis = plot_hi_snr_map(cells, stretch=stretch, snr_max=20)
    try:
        figure.savefig(tmp_path / f'{stretch}.png')
        assert axis.collections[0].norm.vmin == 0
        assert axis.collections[0].norm.vmax == 20
        assert figure.axes[-1]._colorbar.extend == 'max'
        assert [cell['snr'] for cell in cells] == [0,10,100]
    finally:
        plt.close(figure)


def test_diamond_lattice_averages_inside_actual_diamonds_and_wraps():
    # These samples share a rectangle but lie on opposite sides of a diamond edge.
    samples = [dict(l=2, b=2, snr=10), dict(l=4, b=4, snr=30),
               dict(l=359, b=0, snr=20), dict(l=1, b=0, snr=30)]
    diamonds = mean_cells(samples, 10, 10, 'diamond')
    assert diamonds == [dict(l=0, b=0, snr=20, count=3), dict(l=5, b=5, snr=30, count=1)]
    assert len(mean_cells(samples, 10, 10, 'rectangle')) == 1
    for sample in samples:
        # Every source falls inside the diamond that received it, including seam wrap.
        assert any(abs((sample['l']-cell['l']+180) % 360 - 180) / 5
                   + abs(sample['b']-cell['b']) / 5 <= 1 for cell in diamonds)
    polygon = _make_cell_polygons(20, 0, 10, 20, 'diamond')[0]
    np.testing.assert_allclose(np.degrees(polygon.get_xy()[:4]), [[-15,0],[-20,10],[-25,0],[-20,-10]])
    with pytest.raises(ValueError, match='shape'):
        mean_cells(samples, cell_shape='triangle')


@pytest.mark.parametrize('shape', ['rectangle', 'diamond'])
def test_cells_clip_at_mollweide_seam_and_poles(shape):
    polygons = _make_cell_polygons(180, 90, 10, 10, shape)
    assert len(polygons) == 2
    for polygon in polygons:
        vertices = np.degrees(polygon.get_xy())
        assert np.all(np.abs(vertices[:, 0]) <= 180)
        assert np.all(np.abs(vertices[:, 1]) <= 90)
        assert np.ptp(vertices[:, 0]) <= 10


def test_original_measurement_dots_and_click_coordinates_match_saved_png(tmp_path):
    from PIL import Image
    first = aggregate('first', id='aggregate-one')
    second = aggregate('second', id='aggregate-two')
    first.update(longitude=31., latitude=2.)
    second.update(longitude=31., latitude=2.)
    result = dict(observation='test', aggregates=[first, second], views={},
                  distribution=dict(cell_shape='diamond', l_step=10, b_step=10))
    export_distribution(result, tmp_path)
    assert result['distribution']['cell_shape'] == 'diamond'
    points = result['distribution']['points']
    assert len(points) == 2  # Keep both measurements although they share a cell and position.
    assert points[0]['aggregate_id'] == 'aggregate-one'
    assert points[1]['aggregate_id'] == 'aggregate-two'
    assert points[0]['longitude'] == 31 and points[0]['latitude'] == 2
    assert points[0]['x'] == points[1]['x'] and points[0]['y'] == points[1]['y']
    image = np.asarray(Image.open(tmp_path / 'distribution.png').convert('RGB'))
    height, width = image.shape[:2]
    for point in points:
        assert 0 < point['x'] < 1 and 0 < point['y'] < 1
        x, y = round(point['x'] * width), round(point['y'] * height)
        assert np.any(np.all(image[y-3:y+4, x-3:x+4] < 35, axis=2))
    first['velocity_metrics'] = None
    export_distribution(result, tmp_path)
    assert [point['aggregate_id'] for point in result['distribution']['points']] == ['aggregate-two']
