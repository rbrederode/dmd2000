import numpy as np

from obs.opt import _filterbank_trimmed_std, _normalise_filterbank_spectra


def test_filterbank_trimmed_std_matches_julia_sample_std():
    values = np.arange(10, dtype=np.float32)

    assert _filterbank_trimmed_std(values, prop=0.2) == np.std(values[2:8], ddof=1)


def test_normalise_filterbank_spectra_uses_julia_clipping_rules():
    spectra = np.array([[2.0, np.inf, np.nan, 600.0]], dtype=np.float32)

    normalised = _normalise_filterbank_spectra(spectra, trim_std=2.0)

    np.testing.assert_allclose(normalised, np.array([[1.0, 0.0, 0.0, 0.0]], dtype=np.float32))
