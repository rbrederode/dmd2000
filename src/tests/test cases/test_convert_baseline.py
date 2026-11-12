import numpy as np
import pytest

from util.convert import subtract_spectrum_baseline


@pytest.mark.parametrize("method", ["linear", "constant", "none"])
def test_baseline_subtracts_without_scaling_and_preserves_masks(method):
    velocity = np.linspace(-100, 150, 251)
    line = 5 * np.exp(-((velocity - 15) / 10)**2)
    power = np.ma.array(10 + .01 * velocity + line, mask=np.zeros(251, dtype=bool))
    power[0] = 1e6
    power.mask[0] = True
    power[1] = np.nan
    original = power.copy()
    fit_mask = (velocity >= -80) & (velocity <= 100)
    corrected, baseline = subtract_spectrum_baseline(velocity, power, method, fit_mask)
    valid = ~np.ma.getmaskarray(corrected)
    np.testing.assert_allclose(corrected.data[valid], power.data[valid] - baseline.data[valid])
    np.testing.assert_array_equal(power.data, original.data)
    np.testing.assert_array_equal(power.mask, original.mask)
    assert corrected.mask[0] and corrected.mask[1]
    if method == "none":
        np.testing.assert_array_equal(baseline.compressed(), 0)
    else:
        points = fit_mask & np.isfinite(power.filled(np.nan))
        points &= power.filled(np.nan) <= np.quantile(power.data[points], .2)
        expected = (np.polyval(np.polyfit(velocity[points], power.data[points], 1), velocity)
                    if method == "linear" else np.full(251, np.median(power.data[points])))
        np.testing.assert_allclose(baseline.data[valid], expected[valid])


def test_baseline_retains_negative_noise_residuals_and_does_not_require_a_line():
    velocity = np.arange(100.)
    power = np.tile([-1., 1.], 50)
    corrected, baseline = subtract_spectrum_baseline(velocity, power, "none")
    np.testing.assert_array_equal(corrected, power)
    np.testing.assert_array_equal(baseline, 0)
    corrected, baseline = subtract_spectrum_baseline(velocity, np.zeros(100), "linear")
    np.testing.assert_array_equal(corrected, 0)


@pytest.mark.parametrize("velocity,power,mask", [
    (np.arange(5), np.arange(5), None),
    (np.arange(20), np.arange(19), None),
    (np.arange(20), np.arange(20), np.ones(10, dtype=bool)),
    (np.ones(20), np.ones(20), None),
])
def test_bad_baseline_inputs(velocity, power, mask):
    with pytest.raises(ValueError):
        subtract_spectrum_baseline(velocity, power, "linear", mask)


@pytest.mark.parametrize("method", ["linear", "constant"])
def test_line_free_fit_uses_both_ends_and_rejects_outliers(method):
    v = np.arange(-200., 201.)
    expected = 10 + (.01*v if method == "linear" else np.zeros_like(v))
    noise = .01 * np.where(np.arange(len(v)) % 2, 1, -1)
    line = 4*np.exp(-.5*(v/10)**2)
    power = expected + noise + line
    power[v == -180] += 50
    power = np.ma.array(power, mask=v == 180)
    power[v == -160] = np.nan
    mask = (v <= -100) | (v >= 100)
    corrected, baseline = subtract_spectrum_baseline(v, power, method, mask, fit_line_free=True)
    valid = ~np.ma.getmaskarray(corrected)
    np.testing.assert_allclose(baseline.data[valid], expected[valid], atol=.002)
    assert corrected[v == 0][0] == pytest.approx(4 + noise[v == 0][0], abs=.002)
    assert corrected.mask[v == 180][0] and corrected.mask[v == -160][0]
    # Removing baseline must not edit the spectral outlier itself.
    assert corrected[v == -180][0] > 49
