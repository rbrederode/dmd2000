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
