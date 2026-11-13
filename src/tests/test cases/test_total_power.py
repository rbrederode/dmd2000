import csv
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import matplotlib
matplotlib.use("Agg")
from matplotlib import pyplot as plt
import numpy as np
import pytest

from models.scan import ScanType
from obs.total_power import physical_power_records, power_series, timeline_origin, save_total_power, save_power_csv


def observation_data():
    start = datetime(2026, 8, 21, 13, tzinfo=timezone.utc)
    scans = [SimpleNamespace(scan_id=f'sky-{index}', tgt_idx=1, freq_scan=0, scan_iter=index,
                             dig_id='dig001', scan_type=ScanType.SKY, read_start=start+timedelta(seconds=index*10))
             for index in range(2)]
    observation = SimpleNamespace(obs_model=SimpleNamespace(target_scans=[SimpleNamespace(scans=scans)]),
        int_data_arrays={(1, 0, 0): dict(scans=1, secs=2, int_tpw=[10., 20.]),
                         (1, 0, 1): dict(scans=1, secs=3, int_tpw=[30., 40., 50.])})
    return observation


def test_native_seconds_and_scan_means_keep_timestamps_and_physical_boundaries():
    observation = observation_data()
    records = physical_power_records(observation)
    origin = timeline_origin(records)
    key, segments = list(power_series(records, origin=origin))[0]
    assert key == (1, 0, 'dig001', 'SKY')
    np.testing.assert_array_equal(segments[0]['seconds'], [1, 2])
    np.testing.assert_array_equal(segments[1]['seconds'], [11, 12, 13])
    assert [segment['mean'] for segment in segments] == [15, 40]
    assert [segment['scan'] for segment in segments] == [1, 2]
    assert sum(len(segment['power']) for segment in segments) == 5
    assert len(physical_power_records(observation, ['sky-1'])) == 1


def test_missing_timestamp_uses_recorded_seconds_and_unloaded_entries_are_omitted():
    observation = observation_data()
    observation.obs_model.target_scans[0].scans[1].read_start = None
    records = physical_power_records(observation)
    assert timeline_origin(records) is None
    _, segments = list(power_series(records))[0]
    np.testing.assert_array_equal(segments[1]['seconds'], [3, 4, 5])
    observation.int_data_arrays[(1, 0, 1)]['scans'] = 0
    assert len(physical_power_records(observation)) == 1


def test_seconds_and_scan_csv_export_exact_plotted_values(tmp_path):
    records = physical_power_records(observation_data())
    origin = timeline_origin(records)
    for resolution, expected in [('seconds', [10, 20, 30, 40, 50]), ('scans', [15, 40])]:
        path = tmp_path/f'{resolution}.csv'
        save_power_csv(records, path, resolution=resolution, origin=origin)
        with path.open() as stream:
            rows = list(csv.DictReader(stream))
        assert [float(row['total_power']) for row in rows] == expected
        assert rows[-1]['scan_id'] == 'sky-1'
        if resolution == 'seconds':
            assert [float(row['elapsed_or_recorded_seconds']) for row in rows] == [1, 2, 11, 12, 13]
            assert rows[-1]['sample_end_utc'].endswith('13:00:13+00:00')


def test_plots_switch_axes_and_do_not_draw_through_recording_gaps(tmp_path, monkeypatch):
    import obs.total_power as module
    records = physical_power_records(observation_data())
    origin = timeline_origin(records)
    captured = []
    def capture(figure, axis, path):
        captured.append((axis.get_xlabel(), [(line.get_xdata().copy(), line.get_ydata().copy())
                                             for line in axis.lines if line.get_linestyle() != '--']))
    monkeypatch.setattr(module, 'save_velocity_figure', capture)
    save_total_power(records, tmp_path/'seconds.png', 'solar', resolution='seconds', origin=origin)
    save_total_power(records, tmp_path/'scans.png', 'solar', resolution='scans', origin=origin)
    assert captured[0][0] == 'Elapsed observation time [s]'
    assert len(captured[0][1]) == 2  # Separate physical recordings, no connecting line.
    np.testing.assert_array_equal(captured[0][1][1][0], [11, 12, 13])
    assert captured[1][0] == 'Scan number'
    np.testing.assert_array_equal(captured[1][1][0][1], [15, 40])


@pytest.mark.parametrize('resolution', ['seconds', 'scans'])
def test_mean_toggle_only_changes_reference_lines(tmp_path, monkeypatch, resolution):
    import obs.total_power as module
    records = physical_power_records(observation_data())
    captured = []
    def capture(figure, axis, path):
        captured.append([(line.get_linestyle(), np.asarray(line.get_xdata()).copy(),
                          np.asarray(line.get_ydata()).copy()) for line in axis.lines])
    monkeypatch.setattr(module, 'save_velocity_figure', capture)
    for mean in (True, False):
        save_total_power(records, tmp_path/'plot.png', 'solar', resolution=resolution, show_mean=mean)
    assert any(style == '--' for style, _, _ in captured[0])
    assert all(style != '--' for style, _, _ in captured[1])
    signal = [line for line in captured[0] if line[0] != '--']
    assert len(signal) == len(captured[1])
    for before, after in zip(signal, captured[1]):
        np.testing.assert_array_equal(before[1], after[1])
        np.testing.assert_array_equal(before[2], after[2])


def test_affine_corrections_match_direct_flagged_reconstruction_and_are_not_cumulative(tmp_path):
    from obs.obs import Observation
    from obs.total_power import corrected_power_records, save_power_cache, load_power_cache
    from sdp.channel_mask import empty_channel_flags, ChannelFlag, reconstructed_total_power
    from sdp.spectral_baseline import subtract_baseline
    n = 101  # Odd FFT grid also supported.
    frequency = (1413e6 + np.fft.fftshift(np.fft.fftfreq(n, d=1/2.4e6))) / 1e6
    values = 5 + .1*np.arange(n)[None, :] + np.array([0., .7, -.2])[:, None]
    values = values + .01*np.sin(np.arange(n))
    flags = empty_channel_flags(values.shape)
    flags[:, :10] = flags[:, -15:] = ChannelFlag.BANDPASS_EXCLUDED
    flags[0, 30:34] = ChannelFlag.RFI_DETECTED
    flags[1, 12:15] = ChannelFlag.RFI_DETECTED
    flags[2, 65:75] = ChannelFlag.RFI_DETECTED
    values[1, 55] = np.nan
    model = SimpleNamespace(tgt_idx=1, freq_scan=0, scan_iter=0)
    entry = dict(int_spr=np.zeros(n), int_mpr=np.zeros(n), int_tpw=[], secs=0, scans=0)
    observation = SimpleNamespace(cache_power_moments=True, int_data_arrays={(1, 0, 0): entry})
    scan = SimpleNamespace(scan_model=model, spr=values, cal=values, cal_flags=flags,
                           mpr=np.ones(n), get_loaded_seconds=lambda: 3)
    Observation._update_integrated_data_arrays(observation, scan)
    record = dict(scan_id='sky', target_id=1, freq_scan=0, digitiser='dig001', kind='SKY', scan_iter=0,
                  start=None, channels=n, center_freq=1413e6, sample_rate=2.4e6,
                  **{key: np.asarray(entry[key]) for key in ('power_count', 'power_channel')},
                  power=np.asarray(entry['int_tpw']))
    save_power_cache([record], tmp_path)
    cached = load_power_cache(tmp_path)
    for method in ('linear', 'constant', 'flatten'):
        _, correction = subtract_baseline(frequency, values[0], method, fit_line_free=True)
        # Reverse the axis to represent Velocity records' native channel order.
        spectrum = dict(target_id=1, freq_scan=0, digitiser='dig001',
                        frequency_mhz=frequency[::-1], frequency_correction=correction[::-1])
        adjusted = corrected_power_records(cached, [spectrum], method)
        expected, _, _ = reconstructed_total_power(values - correction, flags)
        np.testing.assert_allclose(adjusted[0]['power'], expected, atol=1e-9)
        repeated = corrected_power_records(cached, [spectrum], method)
        np.testing.assert_array_equal(adjusted[0]['power'], repeated[0]['power'])
        np.testing.assert_array_equal(corrected_power_records(cached, [], 'none')[0]['power'], record['power'])
        load_record = {**record, 'kind': 'LOAD'}
        np.testing.assert_array_equal(corrected_power_records([load_record], [], method)[0]['power'], record['power'])
