import csv
import json

import matplotlib
matplotlib.use("Agg")
import numpy as np
import pytest

from ui.opt_reanalysis import reanalyse
from ui.opt_service import atomic_json


@pytest.mark.parametrize('method', ['none', 'linear', 'constant', 'flatten'])
def test_frequency_update_without_pointing_or_velocity_cache(tmp_path, method):
    from sdp.channel_mask import ChannelFlag, empty_channel_flags
    source, out = tmp_path/'source', tmp_path/'updated'
    source.mkdir(); out.mkdir()
    frequency = np.linspace(1411.8, 1414.2, 401)
    power = 5 + .1*(frequency-1413) + .01*np.sin(np.arange(len(frequency)))
    flags = empty_channel_flags(len(power))
    flags[:40] = flags[-40:] = ChannelFlag.BANDPASS_EXCLUDED
    flags[100] = ChannelFlag.RFI_DETECTED
    power[flags != 0] = np.nan
    np.savez_compressed(source/'native-spectra.npz', **{'0_frequency_mhz': frequency, '0_power': power,
                                                      '0_channel_flags': flags})
    atomic_json(source/'native-spectra.json', [dict(id='solar', target_id=2, freq_scan=0,
                                                  center_freq=1413e6, longitude=None, latitude=None,
                                                  velocity_error='No recorded sky coordinates or Alt/Az.')])
    atomic_json(source/'result.json', dict(observation='solar-observation', aggregates=[dict(id='solar', views={})],
                                         views={'frequency': 'frequency.png'}, warnings=[], files=[]))
    (source/'frequency.png').write_bytes(b'unchanged all-SKY Frequency')
    original = (source/'native-spectra.npz').read_bytes()
    result = reanalyse(dict(source_results=str(source), settings=dict(baseline=method,
                       baseline_vmin=None, baseline_vmax=None, frequency_min_mhz=1412., frequency_max_mhz=1414.,
                       contours=None, contour_smoothing=0)), out, lambda *args: None)
    row = result['aggregates'][0]
    assert row['views']['frequency'] == 'solar-frequency.png'
    assert (out/'solar-frequency.png').read_bytes().startswith(b'\x89PNG')
    assert 'velocity' not in row['views']
    assert 'No recorded sky coordinates' in row['view_errors']['velocity']
    assert 'velocity' not in result['views']
    assert not any('baseline-fitted' in message for message in result['warnings'])
    assert (out/'native-spectra.npz').read_bytes() == original
    assert (out/'frequency.png').read_bytes().startswith(b'\x89PNG')
    assert result['views']['frequency'] == 'frequency.png'
    assert (source/'frequency.png').read_bytes() == b'unchanged all-SKY Frequency'


def test_reanalysis_preserves_native_powers_and_signal_products(tmp_path, monkeypatch):
    import obs.opt
    monkeypatch.setattr(obs.opt, "process_observation", lambda *a, **k: (_ for _ in ()).throw(AssertionError("No recorded scan replay")))
    source, out = tmp_path / "source", tmp_path / "updated"
    source.mkdir(); out.mkdir()
    velocity = np.linspace(-100, 150, 251)
    original = 10 + .01 * velocity + 2 * np.exp(-((velocity - 10) / 10)**2)
    original[20] = np.nan
    arrays, labels, aggregates = {}, [], []
    for i in range(2):
        identifier = f"aggregate-{i:04d}"
        arrays[f"{i}_velocity"] = velocity
        arrays[f"{i}_power"] = original + i
        arrays[f"{i}_frequency_mhz"] = 1420.4 - velocity * .004
        labels.append(dict(id=identifier, target_id=i, freq_scan=0, center_freq=1420e6, longitude=30 + i*10, latitude=0))
        aggregates.append(dict(id=identifier, target_id=i, freq_scan=0, views={"signal": f"{identifier}-signal.png", "frequency": f"{identifier}-frequency.png"}))
        (source / f"{identifier}-signal.png").write_bytes(b"unchanged signal image")
        (source / f"{identifier}-frequency.png").write_bytes(b"unchanged frequency image")
        (source / f"{identifier}-timeline.png").write_bytes(b"unchanged total power image")
        (source / f"{identifier}-timeline-seconds.png").write_bytes(b"unchanged per-second total power image")
    (source / "frequency.png").write_bytes(b"all frequency image")
    (source / "timeline-seconds.png").write_bytes(b"all per-second total power image")
    (source / "total-power-seconds.csv").write_text("physical seconds\n")
    (source / "total-power-scans.csv").write_text("physical scan means\n")
    np.savez_compressed(source / "native-spectra.npz", **arrays)
    atomic_json(source / "native-spectra.json", labels)
    snapshot = {"steps_map": {"default": [{"step": "BASELINE", "params": {"context": "cal", "method": "linear"}}]}}
    atomic_json(source / "pipeline-config.json", snapshot)
    aggregates[0]["pipeline_baseline_applied"] = True
    atomic_json(source / "result.json", dict(observation="example", aggregates=aggregates,
                                            pipeline_config=snapshot, views={}, warnings=[], files=[]))
    messages = []
    result = reanalyse(dict(source_results=str(source), settings=dict(baseline="linear", baseline_vmin=-100,
                       baseline_vmax=150, contours=None, contour_smoothing=2)), out, lambda m, f: messages.append(m))
    assert (out / "aggregate-0000-frequency.png").read_bytes().startswith(b"\x89PNG")
    assert result["aggregates"][0]["views"]["frequency"] == "aggregate-0000-frequency.png"
    assert (out / "frequency.png").read_bytes().startswith(b"\x89PNG")
    assert result["views"]["frequency"] == "frequency.png"
    assert (source / "frequency.png").read_bytes() == b"all frequency image"
    assert not (out / "aggregate-0000-timeline.png").exists()
    assert not (out / "timeline-seconds.png").exists()
    assert not (out / "total-power-seconds.csv").exists()
    assert not (out / "total-power-scans.csv").exists()
    assert "Reprocess" in result["view_errors"]["timeline"]
    assert result["pipeline_config"] == snapshot
    assert result["aggregates"][0]["pipeline_baseline_applied"]
    assert (out / "pipeline-config.json").read_bytes() == (source / "pipeline-config.json").read_bytes()
    assert result["analysis_only"]
    assert result["views"]["lv"] == "lv.png"
    assert result["views"]["velocity"] == "velocity.png"
    assert (out / "aggregate-0000-signal.png").read_bytes() == b"unchanged signal image"
    assert (out / "native-spectra.npz").read_bytes() == (source / "native-spectra.npz").read_bytes()
    with (out / "velocity.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 502
    first = rows[:251]
    assert first[20]["calibrated_power"] == first[20]["baseline_subtracted_power"] == ""
    valid = np.isfinite(original)
    np.testing.assert_allclose([float(first[i]["calibrated_power"]) for i in np.flatnonzero(valid)], original[valid])
    corrected = np.array([float(row["baseline_subtracted_power"]) if row["baseline_subtracted_power"] else np.nan for row in first])
    assert np.nanmax(corrected) < 3
    assert messages


@pytest.mark.parametrize("method", ["none", "constant", "linear", "flatten"])
@pytest.mark.parametrize("bounds", [(None, None), (-250, 250)])
def test_reanalysis_matches_initial_velocity_csv_and_controls(tmp_path, monkeypatch, method, bounds):
    from types import SimpleNamespace
    from astropy.coordinates import SkyCoord
    from astropy import units as u
    from obs import lsr
    from obs.obs_display import ObsDisplay
    from matplotlib import pyplot as plt
    from sdp.channel_mask import ChannelFlag, empty_channel_flags
    source, out = tmp_path / "source", tmp_path / "updated"
    source.mkdir(); out.mkdir()
    v = np.arange(-650., 551.)[::-1]
    raw = 10 + .01 * v + 20 * (np.abs(v) < 190) + .1 * np.sin(v)
    raw[10] = np.nan
    flags = empty_channel_flags(len(v))
    flags[(v < -550) | (v > 500)] = ChannelFlag.BANDPASS_EXCLUDED
    flags[v == 100] = ChannelFlag.RFI_DETECTED
    raw[v == 100] = 1e5
    original = raw.copy()
    direction = SkyCoord(l=30*u.deg, b=0*u.deg, frame="galactic")
    monkeypatch.setattr(lsr, "lsr_velocity", lambda *args: v)
    monkeypatch.setattr(lsr, "scan_observing_context", lambda *args: (direction, None, None))
    model = SimpleNamespace(tgt_idx=1, freq_scan=0, center_freq=1420e6, sample_rate=1e6,
                            spectral_resolution=len(v), scan_id="aggregate")
    display = ObsDisplay("example")
    display.scans = [SimpleNamespace(scan_model=model, mpr=raw, mpr_flags=flags)]
    policy = dict(noise_estimator="median_mad", window_frac=.35, smooth_window=5) if bounds[0] is not None else None
    fig = display.display_lsr(None, str(source / "initial.png"), csv_path=str(source / "initial.csv"),
                              observation_baseline=method, vmin=bounds[0], vmax=bounds[1], metric_settings=policy)
    axis = fig.axes[0]
    assert len(axis.lines) == 4  # Spectrum, RFI, vertical zero velocity, median baseline.
    assert len(axis.patches) == 2
    expected_limits = (-650.5, 550.5) if bounds[0] is None else bounds
    np.testing.assert_allclose(axis.get_xlim(), expected_limits)
    initial_ylimits = axis.get_ylim()
    initial_metrics_text = next(text.get_text() for text in axis.texts if text.get_gid() == 'spectrum-noise-metrics')
    initial_rfi = next(line for line in axis.lines if line.get_label() == 'RFI Flagged').get_ydata().copy()
    spans = []
    for patch in axis.patches:
        points = patch.get_patch_transform().transform(patch.get_path().vertices)
        spans.append((points[:, 0].min(), points[:, 0].max()))
    np.testing.assert_allclose(spans, [(-650.5, -550.5), (500.5, 550.5)])
    if method == "none":
        assert fig.axes[0].get_ylim()[0] > 0
    plt.close(fig)
    native = display.lsr_spectra[0]
    np.testing.assert_array_equal(raw, original)
    np.testing.assert_array_equal(native['channel_flags'], flags[::-1])
    np.savez_compressed(source / "native-spectra.npz", **{"0_velocity": native['velocity'],
                        "0_power": native['raw_power'].filled(np.nan), "0_channel_flags": native['channel_flags']})
    atomic_json(source / "native-spectra.json", [dict(id="aggregate", target_id=1, freq_scan=0,
                center_freq=1420e6, longitude=30, latitude=0, qa_settings=policy)])
    atomic_json(source / "result.json", dict(observation="example", aggregates=[dict(id="aggregate", views={})],
                                            views={}, warnings=[], files=[]))
    lv_calls, limits = [], []
    def capture_lv(self, obs, path, contours, smoothing, **kwargs):
        lv_calls.append((contours, smoothing, kwargs))
    monkeypatch.setattr(ObsDisplay, "display_lv", capture_lv)
    import ui.opt_reanalysis as module
    save = module.save_velocity
    def capture_velocity(records, path, observation, baseline, vmin, vmax, **kwargs):
        limits.append((vmin, vmax))
        return save(records, path, observation, baseline, vmin, vmax, **kwargs)
    monkeypatch.setattr(module, "save_velocity", capture_velocity)
    render = module.save_velocity_figure
    def check_velocity_scale(figure, axis, path):
        assert len(axis.lines) == (3 if path.name == 'velocity.png' else 4)
        assert len(axis.patches) == 2
        np.testing.assert_allclose(axis.get_xlim(), expected_limits)
        np.testing.assert_allclose(axis.get_ylim(), initial_ylimits)
        annotations = [text.get_text() for text in axis.texts if text.get_gid() == 'spectrum-noise-metrics']
        assert annotations == ([] if path.name == 'velocity.png' else [initial_metrics_text])
        np.testing.assert_allclose(next(line for line in axis.lines if line.get_label() == 'RFI Flagged').get_ydata(), initial_rfi)
        if method == "none":
            assert axis.get_ylim()[0] > 0
        return render(figure, axis, path)
    monkeypatch.setattr(module, "save_velocity_figure", check_velocity_scale)
    lab_calls = []
    def capture_lab(records, selection, settings, out, result, **kwargs):
        lab_calls.append((records[0]["id"], selection["beam"]))
    monkeypatch.setattr("ui.opt_lab.render_lab_comparison", capture_lab)
    reanalyse(dict(source_results=str(source), lab_comparison=dict(aggregate_id="aggregate", beam=5),
                   settings=dict(baseline=method, baseline_vmin=bounds[0],
              baseline_vmax=bounds[1], contours=[.1, .2], contour_smoothing=0)), out, lambda *args: None)
    with (source / "initial.csv").open() as stream:
        expected = list(csv.DictReader(stream))
    with (out / "velocity.csv").open() as stream:
        actual = list(csv.DictReader(stream))
    assert actual == expected
    assert limits == [bounds, bounds]
    assert lv_calls == [([.1, .2], 0, dict(vmin=bounds[0], vmax=bounds[1]))]
    assert lab_calls == [("aggregate", 5)]
    assert (out / 'native-spectra.npz').read_bytes() == (source / 'native-spectra.npz').read_bytes()


@pytest.mark.parametrize('basis', ['frequency', 'velocity'])
def test_reanalysis_refreshes_total_power_from_active_spectral_correction(tmp_path, basis):
    from obs.total_power import save_power_cache, load_power_cache, corrected_power_records
    from sdp.spectral_baseline import subtract_baseline
    from util.convert import select_baseline_windows
    source, out = tmp_path/'source', tmp_path/'out'
    source.mkdir(); out.mkdir()
    n, rate, centre = 401, 2.4e6, 1420.4e6
    frequency = (centre + np.fft.fftshift(np.fft.fftfreq(n, d=1/rate))) / 1e6
    velocity = (1420.40575 - frequency)*211.
    original = 5 + .2*(frequency-1420.4) + .1*(frequency-1420.4)**2 + .01*np.sin(np.arange(n))
    order = np.argsort(velocity)
    velocity, frequency, original = velocity[order], frequency[order], original[order]
    np.savez_compressed(source/'native-spectra.npz', **{'0_power': original,
        '0_frequency_mhz': frequency, '0_velocity': velocity, '0_channel_flags': np.zeros(n, dtype=np.uint16)})
    atomic_json(source/'native-spectra.json', [dict(id='sky', target_id=1, freq_scan=0, digitiser='dig001',
        center_freq=centre, longitude=30., latitude=0.)])
    row = dict(id='sky', target_id=1, freq_scan=0, digitiser='dig001', kind='SKY', views={})
    atomic_json(source/'result.json', dict(observation='test', aggregates=[row], settings={'baseline':'none'}, views={}, warnings=[]))
    raw = dict(scan_id='physical', target_id=1, freq_scan=0, digitiser='dig001', kind='SKY', scan_iter=0,
        start=None, channels=n, center_freq=centre, sample_rate=rate, power=np.array([2000.,2100.]),
        power_count=np.full(2, n), power_channel=np.full(2, n*(n-1)/2))
    save_power_cache([raw], source)
    settings = dict(baseline='linear', total_power_basis=basis, baseline_vmin=-120., baseline_vmax=150.,
                    frequency_min_mhz=1419.8, frequency_max_mhz=1421.3, contours=None, contour_smoothing=0)
    result = reanalyse(dict(source_results=str(source), settings=settings), out, lambda *args: None)
    low, high = (-120.,150.) if basis == 'velocity' else (1419.8,1421.3)
    coordinates = velocity if basis == 'velocity' else frequency
    midpoint = 0 if basis == 'velocity' else (low+high)/2
    windows = select_baseline_windows(coordinates-midpoint, original, low-midpoint, high-midpoint)
    mask = np.zeros(n, dtype=bool)
    for left, right in windows:
        mask |= (coordinates-midpoint >= left) & (coordinates-midpoint <= right)
    _, correction = subtract_baseline(coordinates, original, 'linear', mask, fit_line_free=True)
    expected = corrected_power_records([raw], [dict(target_id=1, freq_scan=0, digitiser='dig001',
        frequency_mhz=frequency, frequency_correction=correction)], 'linear')[0]['power']
    with (out/'total-power-seconds.csv').open() as stream:
        actual = [float(r['total_power']) for r in csv.DictReader(stream)]
    np.testing.assert_allclose(actual, expected)
    with (out/'total-power-scans.csv').open() as stream:
        assert float(next(csv.DictReader(stream))['total_power']) == pytest.approx(np.mean(expected))
    assert expected[1]-expected[0] == pytest.approx(100.)  # Temporal variation preserved.
    for owner in (result, result['aggregates'][0]):
        for key in ('timeline', 'timeline_seconds'):
            assert (out/owner['views'][key]).read_bytes().startswith(b'\x89PNG')
    np.testing.assert_array_equal(load_power_cache(out)[0]['power'], raw['power'])
    restored = tmp_path/'restored'; restored.mkdir()
    settings['baseline'] = 'none'
    (out/'result.json').write_text(json.dumps(result))
    reanalyse(dict(source_results=str(out), settings=settings), restored, lambda *args: None)
    with (restored/'total-power-seconds.csv').open() as stream:
        np.testing.assert_array_equal([float(r['total_power']) for r in csv.DictReader(stream)], raw['power'])
