import numpy as np
import pytest

from sdp.spectral_metrics import estimate_noise, peak_snr


def test_noise_estimators_have_explicit_distinct_definitions():
    values = np.array([1., 1., 1., 2., 3., 3., 10.])
    rms = estimate_noise(values, estimator='mean_rms')
    mad = estimate_noise(values, estimator='median_mad')
    assert rms.level == 3
    assert rms.scatter == pytest.approx(np.sqrt(62 / 7))
    assert mad.level == 2
    assert mad.scatter == pytest.approx(1.4826)
    assert rms.sample_count == mad.sample_count == 7
    assert rms.estimator == 'mean_rms' and mad.estimator == 'median_mad'


def test_noise_samples_exclude_masks_and_nonfinite_values_without_mutation():
    data = np.ma.array([1., 3., 1e9, np.nan, np.inf], mask=[0, 0, 1, 0, 0])
    original, mask = data.data.copy(), data.mask.copy()
    result = estimate_noise(data, estimator="mean_rms")
    assert (result.level, result.scatter, result.sample_count) == (2, 1, 2)
    np.testing.assert_array_equal(data.data, original)
    np.testing.assert_array_equal(data.mask, mask)


@pytest.mark.parametrize('estimator', ['mean_rms', 'median_mad'])
def test_peak_snr_is_invariant_to_positive_scaling_and_floor_shift(estimator):
    values = np.array([-3., -2., -1., 0., 1., 2., 3.])
    stats = estimate_noise(values, estimator=estimator)
    expected = peak_snr(12., stats.level, stats.scatter)
    transformed = estimate_noise(7 * values - 100, estimator=estimator)
    actual = peak_snr(7 * 12 - 100, transformed.level, transformed.scatter)
    assert actual.linear == pytest.approx(expected.linear)
    assert actual.db == pytest.approx(expected.db)
    assert actual.noise == pytest.approx(7 * expected.noise)


def test_undefined_snr_and_qa_floor_are_explicit():
    with pytest.raises(ValueError, match='positive'):
        peak_snr(0, 0, 0)
    legacy = peak_snr(0, 0, 0, minimum=1e-12)
    assert (legacy.signal, legacy.noise, legacy.linear, legacy.db) == (1e-12, 1e-12, 1., 0.)
    result = peak_snr(12, 2, 1)
    assert (result.signal, result.linear, result.db) == (10., 10., 10.)


@pytest.mark.parametrize('samples,estimator', [([], 'mean_rms'), ([np.nan], 'median_mad'),
                                             ([[1., 2.]], 'mean_rms'), ([1., 2.], 'invalid')])
def test_invalid_noise_requests_fail(samples, estimator):
    with pytest.raises(ValueError):
        estimate_noise(samples, estimator=estimator)
