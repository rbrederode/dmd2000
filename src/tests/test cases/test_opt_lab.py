import json
from types import SimpleNamespace

import matplotlib
matplotlib.use("Agg")
import numpy as np
import pytest
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.io import fits

from ui.opt_lab import render_lab_comparison


def make_record():
    velocity = np.arange(-100., 101.)
    power = 5 * np.exp(-.5 * ((velocity - 10) / 12)**2)
    power[10] = np.nan
    return dict(id="aggregate-0001", target_id=15, freq_scan=0, velocity=velocity,
                power=np.ma.masked_invalid(power), direction=SkyCoord(l=45*u.deg, b=0*u.deg, frame="galactic"))


def test_lab_comparison_uses_cube_resolved_direction_and_exports_without_mutation(tmp_path, monkeypatch):
    record = make_record()
    velocity = record["velocity"]
    line = 50 * np.exp(-.5 * ((velocity - 10) / 12)**2)
    data = np.broadcast_to(line[:, None, None], (201, 5, 9)).copy()
    header = fits.Header(dict(CTYPE1="GLON-CAR", CTYPE2="GLAT-CAR", CTYPE3="VELO-LSR",
                             CUNIT3="m/s", BUNIT="K", CRVAL1=0., CRPIX1=1., CDELT1=45.,
                             CRVAL2=-90., CRPIX2=1., CDELT2=45., CRVAL3=-100000., CRPIX3=1., CDELT3=1000.))
    path = tmp_path / "lab.fit"
    fits.PrimaryHDU(data, header=header).writeto(path)
    monkeypatch.setenv("DMD_LAB_FITS", str(path))
    result = dict(observation="observation", aggregates=[dict(id=record["id"], views={})])
    original = record["power"].copy()
    render_lab_comparison([record], dict(aggregate_id=record["id"], beam=5),
                          dict(baseline="linear", baseline_vmin=-100, baseline_vmax=100), tmp_path, result)
    assert result["aggregates"][0]["views"]["lab"] == "aggregate-0001-lab.png"
    assert (tmp_path / "aggregate-0001-lab.png").read_bytes().startswith(b"\x89PNG")
    metadata = json.loads((tmp_path / "aggregate-0001-lab.json").read_text())
    assert (metadata["longitude"], metadata["latitude"], metadata["beam_fwhm_deg"]) == pytest.approx((45, 0, 5))
    assert metadata["observation_peak_scale"] == pytest.approx(10, rel=.01)
    from sdp.spectral_metrics import observation_noise_metrics
    expected_noise = observation_noise_metrics(velocity, record['power']*metadata['observation_peak_scale'], -100, 100)
    assert metadata['observation_noise']['peak_snr'] == pytest.approx(expected_noise['peak_snr'])
    assert metadata['observation_noise']['noise_rms_k'] == pytest.approx(expected_noise['noise_rms_k'])
    assert metadata['observation_noise']['amplitude_unit'] == 'scaled plot units'
    assert metadata["correlation"] > .999
    assert metadata["pointing_sweep"] is False
    assert (tmp_path / "aggregate-0001-lab.csv").is_file()
    np.testing.assert_array_equal(record["power"], original)


def test_lab_comparison_missing_cube_and_aggregate_are_clear_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("DMD_LAB_FITS", str(tmp_path / "missing.fit"))
    settings = dict(baseline="none", baseline_vmin=-100, baseline_vmax=100)
    with pytest.raises(ValueError, match="cube is missing"):
        render_lab_comparison([make_record()], dict(aggregate_id="aggregate-0001", beam=5), settings, tmp_path, {})
    with pytest.raises(ValueError, match="no usable cached"):
        render_lab_comparison([], dict(aggregate_id="aggregate-0001", beam=5), settings, tmp_path, {})


@pytest.mark.parametrize('lon_range,expected_lon,boundary', [([-3, 3, 1], 47, False), ([0, 1, 1], 46, True)])
def test_sweep_finds_offset_minimum_and_exports_candidate_scores(tmp_path, monkeypatch, lon_range, expected_lon, boundary):
    from ui import opt_lab
    cube = tmp_path / 'lab.fit'; cube.touch()
    monkeypatch.setenv('DMD_LAB_FITS', str(cube))
    class Extractor:
        def __init__(self, path): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def extract(self, longitude, latitude, beam):
            v = np.arange(-100., 101.)
            centre = 10 + (longitude - 47)**2 + (latitude - 1)**2
            return v, 50 * np.exp(-.5 * ((v - centre) / 12)**2)
    monkeypatch.setattr(opt_lab, 'LABGaussianExtractor', Extractor)
    record = make_record()
    original = record['power'].copy()
    result = dict(observation='observation', aggregates=[dict(id=record['id'], views={})])
    sweep = dict(longitude=lon_range, latitude=[-2, 2, 1])
    progress = []
    render_lab_comparison([record], dict(aggregate_id=record['id'], beam=5, sweep=sweep),
                          dict(baseline='linear', baseline_vmin=-100, baseline_vmax=100), tmp_path, result,
                          progress=lambda message, fraction: progress.append((message, fraction)))
    metadata = result['lab_comparison']
    assert metadata['pointing_sweep'] is True
    assert metadata['longitude'] == expected_lon
    assert metadata['latitude'] == 1
    assert metadata['delta_longitude_deg'] == expected_lon - 45
    assert metadata['delta_latitude_deg'] == 1
    assert metadata['boundary_solution'] is boundary
    assert bool(result.get('warnings')) is boundary
    assert metadata['sweep_ranges'] == sweep
    import csv
    with (tmp_path / 'aggregate-0001-lab-sweep.csv').open() as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == (lon_range[1] - lon_range[0] + 1) * 5
    assert all(row['status'] == 'ok' for row in rows)
    assert min(float(row['shape_rms']) for row in rows) == metadata['shape_rms']
    assert progress and progress[-1][0].endswith(f"{len(rows)}/{len(rows)}")
    np.testing.assert_array_equal(record['power'], original)
