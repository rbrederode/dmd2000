from types import SimpleNamespace

import numpy as np
import pytest

from models.pipeline import StepConfig, StepType
from models.scan import ScanType
from sdp.channel_mask import empty_channel_flags, ChannelFlag
from sdp.pipeline.pipeline_factory import ProcessingPipeline
from sdp.pipeline.steps.baseline import Baseline
from sdp.spectral_baseline import subtract_baseline


@pytest.mark.parametrize("method", ["linear", "constant"])
def test_step_shares_fitter_excludes_flagged_channels_and_preserves_inputs(method):
    x = np.linspace(1419., 1421., 201)
    background = 5 + (x - 1420) * (.3 if method == "linear" else 0)
    power = background + 10*np.exp(-((x-1420)/.06)**2)
    flags = empty_channel_flags(power.shape)
    power[0] = 1e8
    flags[0] = ChannelFlag.RFI_DETECTED
    original = power.copy()
    old_flags = flags.copy()
    mask = (x < 1419.2) | (x > 1420.8)
    mask[0] = False
    step = Baseline(StepConfig(step=StepType.BASELINE, params={"context": "cal", "method": method}))
    corrected = step.process({"baseline_x": x, "baseline_fit_mask": mask, "channel_flags": flags}, power)
    shared, baseline = subtract_baseline(x, power, method, mask, fit_line_free=True)
    np.testing.assert_allclose(corrected, shared)
    np.testing.assert_allclose(baseline, background, atol=1e-10)
    np.testing.assert_array_equal(power, original)
    np.testing.assert_array_equal(flags, old_flags)


@pytest.mark.parametrize("reason", ["none", "disabled", "load"])
def test_bypass_preserves_spectrum(reason):
    power = np.arange(3.)
    params = {"context": "cal", "method": "none" if reason == "none" else "linear",
              "enabled": reason != "disabled"}
    if reason == "load":
        params["scan"] = SimpleNamespace(get_scan_type=lambda: ScanType.LOAD)
    step = Baseline(StepConfig(step=StepType.BASELINE, params=params))
    result = ProcessingPipeline([step]).process({"pipeline": "cal"}, power)
    assert result is power


@pytest.mark.parametrize("method", ["linear", "flatten"])
def test_default_outer_channels_follow_valid_bandpass(method):
    x = np.arange(201.)
    power = 4 + .02*x + 10*np.exp(-((x-100)/8)**2)
    flags = empty_channel_flags(power.shape)
    flags[:20] = flags[-20:] = ChannelFlag.BANDPASS_EXCLUDED
    power[:20] = power[-20:] = 1e6
    params = {"context": "cal"}
    if method == "linear":
        params["method"] = method
    step = Baseline(StepConfig(step=StepType.BASELINE, params=params))
    result = step.process({"channel_flags": flags}, power)
    retained = 4 + .02*20 if method == "flatten" else 0
    np.testing.assert_allclose(result[20:-20], (power-4-.02*x+retained)[20:-20], atol=1e-10)


@pytest.mark.parametrize("params", [{"context": "mpr"}, {"context": "cal", "method": "bad"},
                                      {"context": "cal", "ranges_pct": [[100, 0]]}])
def test_reject_invalid_step(params):
    with pytest.raises(ValueError):
        Baseline(StepConfig(step=StepType.BASELINE, params=params))


@pytest.mark.parametrize("slope", [-.02, 0., .02])
@pytest.mark.parametrize("reverse", [False, True])
def test_flatten_retains_fitted_passband_minimum_and_continuum(slope, reverse):
    x = np.arange(201.)
    if reverse:
        x = x[::-1]
    background = 5 + slope*x
    emission = 10*np.exp(-((x-100)/8)**2)
    power = background + emission
    flags = empty_channel_flags(power.shape)
    usable = (x >= 20) & (x <= 180)
    flags[~usable] = ChannelFlag.BANDPASS_EXCLUDED
    power[~usable] = 1e6
    # A low measured outlier outside the fitting windows must not set the level.
    power[x == 100] = -2
    original = power.copy()
    step = Baseline(StepConfig(step=StepType.BASELINE, params={"context": "cal", "method": "flatten"}))
    result = step.process({"baseline_x": x, "channel_flags": flags}, power)
    retained = background[usable].min()
    np.testing.assert_allclose(result[usable], (power-background+retained)[usable], atol=1e-9)
    assert result[x == 100][0] < 0  # No clipping of negative residuals.
    brighter = step.process({"baseline_x": x, "channel_flags": flags}, power+7)
    np.testing.assert_allclose(brighter[usable], result[usable]+7, atol=1e-9)
    np.testing.assert_array_equal(power, original)
    np.testing.assert_array_equal(flags[~usable], ChannelFlag.BANDPASS_EXCLUDED)


@pytest.mark.parametrize("fit_line_free", [False, True])
def test_shared_flatten_preserves_masks_and_returns_applied_correction(fit_line_free):
    x = np.arange(101.)
    power = np.ma.array(10 + .1*x, mask=x == 0)
    power[1] = np.nan
    fit = (x >= 20) & (x <= 80)
    corrected, correction = subtract_baseline(x, power, "flatten", fit, fit_line_free=fit_line_free)
    # The retained level is at the first usable channel, outside the fit windows.
    np.testing.assert_allclose(corrected.compressed(), 10.2, atol=1e-10)
    np.testing.assert_allclose(correction.compressed(), (.1*(x-2))[2:], atol=1e-10)
    assert corrected.mask[0] and corrected.mask[1]
    np.testing.assert_allclose((corrected+correction).compressed(), power[2:].compressed())


def test_flatten_preserves_load_reference():
    power = np.arange(20.)
    step = Baseline(StepConfig(step=StepType.BASELINE, params={
        "context": "cal", "method": "flatten", "scan": SimpleNamespace(get_scan_type=lambda: ScanType.LOAD)}))
    assert step.process({}, power) is power
