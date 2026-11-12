import numpy as np

from obs.opt import _filterbank_trimmed_std, _normalise_filterbank_spectra


def test_filterbank_trimmed_std_matches_julia_sample_std():
    values = np.arange(10, dtype=np.float32)

    assert _filterbank_trimmed_std(values, prop=0.2) == np.std(values[2:8], ddof=1)


def test_normalise_filterbank_spectra_uses_julia_clipping_rules():
    spectra = np.array([[2.0, np.inf, np.nan, 600.0]], dtype=np.float32)

    normalised = _normalise_filterbank_spectra(spectra, trim_std=2.0)

    np.testing.assert_allclose(normalised, np.array([[1.0, 0.0, 0.0, 0.0]], dtype=np.float32))


def test_opt_baseline_cli_defaults_and_overrides(monkeypatch):
    import sys
    import pytest
    from obs.opt import init_args
    monkeypatch.setattr(sys, "argv", ["opt", "--obs", "example"])
    args = init_args()
    assert (args.vmin, args.vmax, args.baseline_auto) == (-100, 100, True)
    for names in [("--vmin", "--vmax"), ("--baseline-vmin", "--baseline-vmax")]:
        monkeypatch.setattr(sys, "argv", ["opt", "--obs", "example", names[0], "-250", names[1], "250"])
        args = init_args()
        assert (args.vmin, args.vmax) == (-250, 250)
    monkeypatch.setattr(sys, "argv", ["opt", "--obs", "example", "--vmin", "10"])
    with pytest.raises(SystemExit):
        init_args()
    for override in [["--no-baseline-auto"], ["--baseline-window", "100", "150"],
                     ["--observation-baseline", "none"]]:
        monkeypatch.setattr(sys, "argv", ["opt", "--obs", "example", "--vmin", "10"] + override)
        assert init_args().vmin == 10
    for bounds in [["--vmin", "nan"], ["--vmax", "-200"]]:
        monkeypatch.setattr(sys, "argv", ["opt", "--obs", "example"] + bounds)
        with pytest.raises(SystemExit):
            init_args()
