from types import SimpleNamespace

import matplotlib
import numpy as np
import pytest

matplotlib.use("Agg")

from matplotlib import pyplot as plt
from matplotlib.colors import to_rgba
from matplotlib.ticker import AutoMinorLocator, MaxNLocator

from obs.obs_display import ObsDisplay, SCAN_COLOURS
from sdp.channel_mask import ChannelFlag, empty_channel_flags


def test_integrated_total_power_uses_readable_ticks_and_non_yellow_colours(tmp_path):
    cal_flags = empty_channel_flags((134, 4))
    cal_flags[:, 2:] |= int(ChannelFlag.BANDPASS_EXCLUDED)
    cal_flags[:, 1] |= int(ChannelFlag.RFI_DETECTED)
    scan = SimpleNamespace(
        scan_model=SimpleNamespace(scan_id="obs-2-0", tgt_idx=2, center_freq=1.42e9, freq_scan=0),
        cal=np.tile(np.array([1.0, 100.0, 1.0, 1.0]), (134, 1)),
        cal_flags=cal_flags,
        get_loaded_seconds=lambda: 134,
    )
    display = ObsDisplay("test-observation")
    display.scans = [scan]
    display._create_figure()

    display._plot_velocity_panel()
    display.fig.canvas.draw()

    assert isinstance(display.ax_vel.xaxis.get_major_locator(), MaxNLocator)
    assert isinstance(display.ax_vel.xaxis.get_minor_locator(), AutoMinorLocator)
    visible_ticks = [tick for tick in display.ax_vel.get_xticks() if 1 <= tick <= 134]
    visible_minor_ticks = [tick for tick in display.ax_vel.xaxis.get_minorticklocs() if 1 <= tick <= 134]
    assert len(visible_ticks) < 20
    assert len(visible_minor_ticks) > len(visible_ticks)
    assert all(float(tick).is_integer() for tick in visible_ticks)
    assert display.ax_vel.lines[0].get_color() == SCAN_COLOURS[0]
    np.testing.assert_array_equal(display.ax_vel.lines[0].get_ydata(), np.full(134, 2.0))
    assert all(to_rgba(colour)[:3] != to_rgba("yellow")[:3] for colour in SCAN_COLOURS)

    output_path = tmp_path / "observation-id-sky-apr.png"
    assert display.save_integrated_total_power(str(output_path)) == str(output_path)
    assert output_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")

    plt.close(display.fig)


def test_observation_spectrum_masks_flags_and_explains_them():
    mpr_flags = empty_channel_flags(6)
    mpr_flags[[0, 1, 5]] |= int(ChannelFlag.BANDPASS_EXCLUDED)
    mpr_flags[3] |= int(ChannelFlag.RFI_DETECTED)
    scan = SimpleNamespace(
        scan_model=SimpleNamespace(
            scan_id="obs-2-0", tgt_idx=2,
            center_freq=1420.4e6,
            sample_rate=6.0e6,
            spectral_resolution=6,
            freq_scan=0,
        ),
        mpr=np.array([1.0, 2.0, 3.0, 40.0, 5.0, 6.0]),
        mpr_flags=mpr_flags,
    )
    display = ObsDisplay("test-observation")
    display.scans = [scan]
    display._create_figure()

    # A requested 1 MHz bandwidth must not crop the full sampled 6 MHz span.
    display.freq_min_mhz, display.freq_max_mhz = 1419.9, 1420.9
    display._plot_frequency_panel()
    display.fig.canvas.draw()
    assert display.ax_freq.get_xlim() == (1417.4, 1423.4)
    assert display.ax_freq.xaxis.get_offset_text().get_text() == ""
    np.testing.assert_allclose(display.ax_freq.lines[0].get_xdata(),
                               [1417.4, 1418.4, 1419.4, 1420.4, 1421.4, 1422.4])

    spectrum_line = display.ax_freq.lines[0]
    rfi_line = display.ax_freq.lines[1]
    np.testing.assert_array_equal(
        np.ma.getmaskarray(spectrum_line.get_ydata()),
        [True, True, False, True, False, True],
    )
    assert spectrum_line.get_label() == "Tgt 2, Freq 0"
    assert rfi_line.get_label() == "RFI Flagged"
    np.testing.assert_array_equal(rfi_line.get_ydata(), [4.0])
    assert len(display.ax_freq.patches) == 2
    legend = display.ax_freq.get_legend()
    assert legend.get_window_extent().x0 > display.ax_freq.get_window_extent().x1
    assert {'RFI Flagged', 'Bandpass Excluded', 'HI rest (1420.406 MHz)'} <= {
        text.get_text() for text in legend.get_texts()}


    plt.close(display.fig)


def test_total_power_31_scan_legend_does_not_compress_data_axes(tmp_path, monkeypatch):
    display = ObsDisplay('31-scans')
    display.scans = [SimpleNamespace(
        scan_model=SimpleNamespace(scan_id=f'obs-{i}-0', tgt_idx=i, freq_scan=0),
        cal=np.full((4, 4), 100. + i), cal_flags=empty_channel_flags((4, 4)),
        get_loaded_seconds=lambda: 4,
    ) for i in range(1, 32)]
    closed = []
    real_close = plt.close
    monkeypatch.setattr(plt, 'close', lambda figure: closed.append(figure))
    output = tmp_path / '31-scans.png'
    display.save_integrated_total_power(str(output))
    figure = closed[-1]
    try:
        figure.canvas.draw()
        axis = figure.axes[0]
        assert axis.get_position().height > .75
        assert axis.get_position().width > .85
        legend = axis.get_legend()
        assert len(legend.get_texts()) == 31
        assert legend.get_window_extent().x0 > axis.get_window_extent().x1
        assert legend.get_window_extent().height <= axis.get_window_extent().height
        data_lines = [line for line in axis.lines if line.get_label().startswith('Tgt ')]
        assert len({(line.get_color(), line.get_linestyle()) for line in data_lines}) == 31
        assert output.read_bytes().startswith(b'\x89PNG')
    finally:
        real_close(figure)


def test_standalone_frequency_has_one_panel_and_preserves_samples(tmp_path, monkeypatch):
    display = ObsDisplay('frequency-only')
    power = np.arange(8, dtype=float)
    display.scans = [SimpleNamespace(scan_model=SimpleNamespace(scan_id='obs-3-0', tgt_idx=3, freq_scan=0, center_freq=1420e6,
                    sample_rate=6e6, spectral_resolution=8), mpr=power.copy(), mpr_flags=empty_channel_flags(8))]
    closed, real_close = [], plt.close
    monkeypatch.setattr(plt, 'close', lambda fig: closed.append(fig))
    path = tmp_path / 'frequency.png'
    display.save_frequency_spectrum(str(path))
    fig = closed[-1]
    try:
        assert len(fig.axes) == 1
        assert fig.axes[0].get_xlabel() == 'Frequency (MHz)'
        assert fig.axes[0].get_title() == 'Aggregated Sky Scans'
        assert not any(text.get_gid() == 'spectrum-noise-metrics' for text in fig.axes[0].texts)
        np.testing.assert_array_equal(display.scans[0].mpr, power)
        assert display.ax_freq is None
        assert path.read_bytes().startswith(b'\x89PNG')
    finally:
        real_close(fig)



@pytest.mark.parametrize('level', [.85, -.85])
def test_single_sky_frequency_scale_matches_all_sky(tmp_path, monkeypatch, level):
    from obs.obs_display import save_frequency_record
    n = 201
    power = level + .02*np.sin(np.arange(n))
    flags = empty_channel_flags(n)
    flags[:20] = flags[-20:] = ChannelFlag.BANDPASS_EXCLUDED
    power[:20] = power[-20:] = 1e6
    model = SimpleNamespace(scan_id='obs-1-0', tgt_idx=1, freq_scan=0, center_freq=1420e6,
                            sample_rate=6e6, spectral_resolution=n)
    display = ObsDisplay('single-sky')
    display.scans = [SimpleNamespace(scan_model=model, mpr=power.copy(), mpr_flags=flags)]
    closed, real_close = [], plt.close
    monkeypatch.setattr(plt, 'close', lambda fig: closed.append(fig))
    try:
        display.save_frequency_spectrum(str(tmp_path / 'all.png'))
        all_axes = closed[-1].axes[0]
        frequency = (model.center_freq + np.fft.fftshift(np.fft.fftfreq(n, d=1/model.sample_rate)))/1e6
        save_frequency_record(dict(frequency_mhz=frequency, raw_power=np.ma.array(power, mask=flags != 0),
                                   power=power, target_id=1, freq_scan=0), tmp_path / 'selected.png', 'single-sky', 'none')
        selected_axes = closed[-1].axes[0]
        assert len(selected_axes.lines) == 3  # Spectrum, median baseline and HI rest-frequency marker.
        np.testing.assert_allclose(selected_axes.get_ylim(), all_axes.get_ylim())
        low, high = selected_axes.get_ylim()
        assert not low <= 0 <= high
        np.testing.assert_allclose(selected_axes.lines[0].get_ydata().compressed(),
                                   all_axes.lines[0].get_ydata().compressed())
        np.testing.assert_array_equal(display.scans[0].mpr, power)
    finally:
        for fig in closed:
            real_close(fig)


@pytest.mark.parametrize('method', ['none', 'linear', 'constant', 'flatten'])
def test_frequency_baseline_and_mhz_bounds_preserve_raw_samples(tmp_path, monkeypatch, method):
    from obs.obs_display import save_frequency_record
    frequency = np.linspace(1419.4, 1421.4, 401)
    baseline = 10 + (2 * (frequency - 1420.4) if method != 'constant' else 0)
    emission = 3 * np.exp(-.5 * ((frequency - 1420.4) / .02)**2)
    raw = np.ma.array(baseline + emission, mask=False)
    raw.mask[15] = True
    original = raw.copy()
    closed, real_close = [], plt.close
    monkeypatch.setattr(plt, 'close', lambda fig: closed.append(fig))
    # Frequency renderer must use raw data rather than subtracting from an
    # already velocity-corrected spectrum a second time.
    record = dict(frequency_mhz=frequency[::-1], raw_power=raw[::-1], power=raw[::-1] - 999,
                  target_id=1, freq_scan=0)
    save_frequency_record(record, tmp_path / 'frequency.png', 'example', method, fmin=1419.8, fmax=1421.)
    fig = closed[-1]
    try:
        axis = fig.axes[0]
        np.testing.assert_allclose(axis.get_xlim(), [1419.8, 1421.])
        expected = original if method == 'none' else np.ma.array(emission, mask=raw.mask)
        if method == 'flatten':
            expected = expected + baseline[~raw.mask].min()
        np.testing.assert_allclose(axis.lines[0].get_ydata().compressed(), expected.compressed(), atol=1e-10)
        np.testing.assert_array_equal(raw, original)
        assert axis.xaxis.get_offset_text().get_text() == ''
        marker = next(line for line in axis.lines if line.get_label().startswith('HI rest'))
        np.testing.assert_allclose(marker.get_xdata(), [1420.405751768, 1420.405751768])
        assert marker.get_linestyle() == '--'

    finally:
        real_close(fig)


@pytest.mark.parametrize('method', ['none', 'linear', 'constant', 'flatten'])
@pytest.mark.parametrize('bounds', [(None, None), (1412.2, 1413.8)])
def test_frequency_overlay_uses_exact_individual_corrections_without_metrics(tmp_path, monkeypatch, method, bounds):
    import obs.obs_display as module
    frequency = np.linspace(1411.8, 1414.2, 401)
    flags = empty_channel_flags(len(frequency))
    flags[:40] = flags[-40:] = ChannelFlag.BANDPASS_EXCLUDED
    flags[100] = ChannelFlag.RFI_DETECTED
    captured = {}
    def capture(figure, axis, path):
        captured[path.name] = dict(
            curves=[(line.get_xdata().copy(), line.get_ydata().copy()) for line in axis.lines
                    if line.get_label().startswith('Tgt ')],
            diagnostics=[text.get_text() for text in axis.texts if text.get_gid() == 'spectrum-noise-metrics'],
            baseline_lines=[line for line in axis.lines if line.get_label() == 'Median noise baseline'],
            patches=len(axis.patches), limits=axis.get_xlim(), title=axis.get_title())
    monkeypatch.setattr(module, 'save_velocity_figure', capture)
    prepared = []
    originals = []
    for target in (1, 2):
        slope = .1 if target == 1 else -.2
        raw = np.ma.array(5*target + slope*(frequency-1413) + 3*np.exp(-((frequency-1413)/.03)**2), mask=flags != 0)
        originals.append(raw.copy())
        record = dict(target_id=target, freq_scan=0, frequency_mhz=frequency, raw_power=raw,
                      power=raw, channel_flags=flags)
        prepared.append(module.save_frequency_record(record, tmp_path/f'{target}.png', 'solar', method,
                                                      fmin=bounds[0], fmax=bounds[1]))
        np.testing.assert_array_equal(raw, originals[-1])
    module.save_frequency_overlay(prepared, tmp_path/'overlay.png', 'solar', method)
    overlay = captured['overlay.png']
    assert overlay['diagnostics'] == overlay['baseline_lines'] == []
    assert overlay['patches'] == 2  # Identical bandpass regions are shaded only once.
    assert f'baseline: {method}' in overlay['title']
    for index, target in enumerate((1, 2)):
        individual = captured[f'{target}.png']
        assert individual['diagnostics'] and individual['baseline_lines']
        np.testing.assert_array_equal(overlay['curves'][index][0], individual['curves'][0][0])
        np.testing.assert_array_equal(overlay['curves'][index][1], individual['curves'][0][1])
        np.testing.assert_allclose(overlay['limits'], individual['limits'])
