import matplotlib
matplotlib.use("Agg")
from matplotlib import pyplot as plt
import numpy as np
import pytest

from obs.spectral_annotations import annotate_spectral_metrics
from sdp.channel_mask import ChannelFlag, empty_channel_flags, masked_values
from sdp import spectral_metrics
from util import lab_profiler


def test_profiler_and_plots_share_profile_diagnostic_and_estimators(monkeypatch):
    assert lab_profiler.observation_noise_metrics is spectral_metrics.observation_noise_metrics
    assert lab_profiler.estimate_profile_noise is spectral_metrics.estimate_profile_noise
    calls = []
    estimate, snr = spectral_metrics.estimate_noise, spectral_metrics.peak_snr
    def capture_noise(*args, **kwargs):
        calls.append('noise')
        return estimate(*args, **kwargs)
    def capture_snr(*args, **kwargs):
        calls.append('snr')
        return snr(*args, **kwargs)
    monkeypatch.setattr(spectral_metrics, 'estimate_noise', capture_noise)
    monkeypatch.setattr(spectral_metrics, 'peak_snr', capture_snr)
    velocity = np.arange(-100., 101.)
    frequency = 1420.4 - .004*velocity
    power = 5 + .2*np.sin(velocity) + 10*np.exp(-(velocity/8)**2)
    flags = empty_channel_flags(len(power))
    flags[velocity == 100] = ChannelFlag.RFI_DETECTED
    flags[velocity < -90] = ChannelFlag.BANDPASS_EXCLUDED
    power[velocity == 100] = 1e9
    original = power.copy()
    figure, axes = plt.subplots(1, 2)
    try:
        observed = annotate_spectral_metrics(axes[0], velocity, power, -100, 100,
                                             label='SKY', flags=flags)
        reversed_frequency = annotate_spectral_metrics(axes[1], frequency[::-1], power[::-1],
                                                        frequency.min(), frequency.max(), label='SKY',
                                                        unit='MHz', flags=flags[::-1], velocity=velocity[::-1])
        assert calls == ['noise', 'snr', 'noise', 'snr']
        expected = spectral_metrics.observation_noise_metrics(velocity, masked_values(power, flags), -100, 100)
        for name in ('noise_level_k', 'noise_rms_k', 'noise_sample_count', 'peak_snr', 'peak_snr_db'):
            assert observed[name] == pytest.approx(expected[name])
            assert reversed_frequency[name] == pytest.approx(expected[name])
        expected_ranges = sorted([[1420.4-.004*high, 1420.4-.004*low] for low, high in observed['noise_ranges']])
        np.testing.assert_allclose(sorted(reversed_frequency['noise_ranges']), expected_ranges)
        assert 'Peak SNR:' in axes[0].texts[0].get_text()
        assert 'Robust Noise:' in axes[1].texts[0].get_text()
        assert 'MHz' in axes[1].texts[0].get_text()
        np.testing.assert_array_equal(power, original)
    finally:
        plt.close(figure)


@pytest.mark.parametrize('size,status', [(2, 'insufficient_noise_channels'), (201, 'zero_noise_rms')])
def test_unavailable_noise_is_labelled_without_inventing_snr(size, status):
    velocity = np.linspace(-100, 100, size)
    figure, axis = plt.subplots()
    try:
        metrics = annotate_spectral_metrics(axis, velocity, np.zeros(size), -100, 100, label='SKY')
        assert metrics['status'] == status
        assert metrics['peak_snr'] is None
        assert 'unavailable' in axis.texts[0].get_text()
    finally:
        plt.close(figure)


@pytest.mark.parametrize('unit', ['km/s', 'MHz'])
def test_restricted_plot_metrics_equal_processing_only_selected_spectrum(unit):
    velocity = np.arange(-500., 501.)
    frequency = 1420.4 - .004*velocity
    power = 5 + .1*np.sin(velocity) + 10*np.exp(-(velocity/8)**2)
    flags = empty_channel_flags(len(power))
    flags[velocity == 30] = ChannelFlag.RFI_DETECTED
    restricted = (velocity >= -100) & (velocity <= 100)
    power[~restricted] = 1e9  # Must not affect peak, noise or smoothing.
    policy = dict(window_frac=.2, smooth_window=10)
    expected = spectral_metrics.observation_noise_metrics(
        velocity[restricted], masked_values(power, flags)[restricted], -100, 100, **policy)
    figure, axis = plt.subplots()
    try:
        coordinates = velocity if unit == 'km/s' else frequency
        low, high = (-100, 100) if unit == 'km/s' else (frequency[restricted].min(), frequency[restricted].max())
        measured = annotate_spectral_metrics(axis, coordinates, power, low, high,
                                             label='SKY', unit=unit, velocity=velocity,
                                             flags=flags, qa_settings=policy)
        for key in ('noise_level_k', 'noise_rms_k', 'peak_snr', 'peak_snr_db', 'noise_sample_count'):
            assert measured[key] == pytest.approx(expected[key])
        assert measured['noise_sample_count'] < restricted.sum()
        for left, right in measured['noise_ranges']:
            assert low <= left <= right <= high
    finally:
        plt.close(figure)
