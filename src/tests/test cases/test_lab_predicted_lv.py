import csv
import json

import matplotlib
matplotlib.use("Agg")
from matplotlib import pyplot as plt
import numpy as np
import pytest
from astropy.io import fits

from util import lab_profiler as lab


@pytest.fixture
def cube(tmp_path):
    velocity = np.linspace(-60, 60, 61)
    data = (np.exp(-((velocity[:, None, None] - 10) / 15)**2) *
            (10 + np.arange(37)[None, None, :]) * np.ones((1, 3, 1)))
    data[0] = np.nan
    header = fits.Header()
    header.update(CTYPE1="GLON-CAR", CRVAL1=0., CRPIX1=1., CDELT1=10.,
                  CTYPE2="GLAT-CAR", CRVAL2=-90., CRPIX2=1., CDELT2=90.,
                  CTYPE3="VELO-LSR", CRVAL3=-60000., CRPIX3=1., CDELT3=2000.,
                  CUNIT3="m/s", BUNIT="K")
    path = tmp_path / "lab.fit"
    fits.PrimaryHDU(data, header=header).writeto(path)
    return path


def test_prediction_matches_gaussian_extraction_and_final_beam(cube):
    longitudes = np.array([20, 30, 40])
    with lab.LABGaussianExtractor(cube) as extractor:
        velocity, brightness = lab.predict_lv(extractor, longitudes, 0, 15, .6)
        assert brightness.shape == (61, 3)
        assert np.isnan(brightness[0]).all()
        for column, longitude in enumerate(longitudes):
            v, t = extractor.extract(longitude, 0, np.sqrt(15**2 - .6**2))
            np.testing.assert_array_equal(velocity, v)
            np.testing.assert_allclose(brightness[:, column], t)


def test_predicted_lv_cli_saves_native_kelvin_data_and_context(cube, tmp_path):
    output = tmp_path / "predicted.csv"
    assert lab.main(["--lv", "--lv-style", "inferno", "--lon", "30", "--lat", "0", "--lon-range", "[-10,10,10]",
                     "--beam", "15", "--lab-fits", str(cube), "--vmin", "-30", "--vmax", "50",
                     "--location", "53", "-2", "77", "--output", str(output), "--no-show"]) == 0
    metadata = json.loads(output.with_suffix(".json").read_text())
    assert metadata["mode"] == "predicted_lv"
    assert metadata["longitudes_deg"] == [20, 30, 40]
    assert metadata["longitude_offsets_deg"] == [-10, 0, 10]
    assert metadata["latitude_deg"] == 0
    assert metadata["final_beam_fwhm_deg"] == 15
    assert metadata["smoothing_kernel_fwhm_deg"] == pytest.approx(np.sqrt(15**2 - .6**2))
    assert metadata["colormap"] == "inferno"
    assert metadata["baseline_subtraction"] == metadata["amplitude_scaling"] == "none"
    assert metadata["location"]["latitude_deg"] == 53
    with output.open() as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 3 * 61  # Full cube coverage is exported, even outside display.
    np.testing.assert_array_equal([float(r["velocity_lsr_kms"]) for r in rows[:61]], np.linspace(-60, 60, 61))
    with lab.LABGaussianExtractor(cube) as extractor:
        _, expected = extractor.extract(20, 0, np.sqrt(15**2 - .6**2))
    np.testing.assert_allclose([float(r["brightness_K"]) for r in rows[:61]], expected)
    assert output.with_suffix(".png").read_bytes().startswith(b"\x89PNG")


def test_lv_display_only_wraps_longitudes_and_does_not_write(cube, tmp_path, monkeypatch):
    captured = []
    monkeypatch.setattr(plt, "show", lambda: captured.append(plt.gcf()))
    before = set(tmp_path.iterdir())
    assert lab.main(["--lv", "--lv-style", "inferno", "--galactic", "359", "0", "--lon-range", "[0,2,1]",
                     "--lab-fits", str(cube), "--display-only", "--output-dir", str(tmp_path)]) == 0
    assert set(tmp_path.iterdir()) == before
    fig = captured[0]
    axis = fig.axes[0]
    assert "359.000° to 1.000° (across 0°)" in axis.get_title()
    assert "longitude" in axis.get_xlabel()
    assert "LSR velocity" in axis.get_ylabel()
    assert "[K]" in fig.axes[-1].get_ylabel()
    assert axis.collections[0].get_cmap().name == "inferno"
    assert axis.xaxis.get_major_formatter()(360, 0) == "0°"


def test_full_circle_does_not_duplicate_the_endpoint(monkeypatch):
    captured = []
    monkeypatch.setattr(lab, "plot_predicted_lv", lambda args, context, offsets: captured.append(offsets))
    assert lab.main(["--lv", "--lon", "0", "--lat", "0", "--lon-range", "[0,360,120]"]) == 0
    np.testing.assert_array_equal(captured[0], [0, 120, 240])


@pytest.mark.parametrize("options", [
    ["--file", "obs.csv", "--target-id", "5", "--freq-scan", "0"],
    ["--sweep"], ["--beam", ".5"], ["--lab-native-beam", "nan"],
    ["--lab-native-beam", "-1"], ["--lon-range", "[0,361,1]"],
    ["--lon-range", "[0,20,0]"],
])
def test_invalid_prediction_inputs_do_not_open_cube(options, monkeypatch):
    monkeypatch.setattr(lab, "LABGaussianExtractor", lambda *a: pytest.fail("No cube access"))
    with pytest.raises(SystemExit) as exc:
        lab.main(["--lv", "--lon", "50", "--lat", "0", *options])
    assert exc.value.code == 2


def test_empty_velocity_display_range_is_reported(cube, tmp_path):
    with pytest.raises(SystemExit) as exc:
        lab.main(["--lv", "--lon", "50", "--lat", "0", "--lab-fits", str(cube),
                  "--vmin", "200", "--vmax", "300", "--output", str(tmp_path / "missing.csv"), "--no-show"])
    assert exc.value.code == 1
    assert not (tmp_path / "missing.png").exists()


def test_reference_style_contours_and_colour_mapping_preserve_temperatures(cube, tmp_path, monkeypatch):
    from matplotlib.colors import AsinhNorm
    captured = []
    monkeypatch.setattr(plt, "show", lambda: captured.append(plt.gcf()))
    output = tmp_path / "reference.csv"
    options = ["--lv", "--lon", "30", "--lat", "0", "--lon-range", "[-10,10,10]",
               "--beam", "15", "--lab-fits", str(cube), "--vmin", "-30", "--vmax", "50",
               "--lv-contours", "5", "10", "20", "--lv-brightness-max", "40"]
    assert lab.main([*options, "--output", str(output)]) == 0
    metadata = json.loads(output.with_suffix(".json").read_text())
    assert metadata["lv_style"] == "reference"
    assert metadata["colormap"] == "lab_white_blue"
    assert metadata["colour_stretch"] == "asinh"
    assert metadata["colour_limits_k"] == [0, 40]
    assert metadata["contour_levels_k"] == [5, 10]  # 20 K is outside this fixture's data range.
    assert metadata["longitude_order"] == "decreasing"
    axis = captured[0].axes[0]
    assert axis.xaxis_inverted()
    assert axis.get_facecolor() == (1, 1, 1, 1)
    assert isinstance(axis.collections[0].norm, AsinhNorm)
    np.testing.assert_array_equal(captured[0].axes[-1].get_yticks(), [0, 5, 10, 20, 40])
    np.testing.assert_array_equal(axis.collections[1].levels, [5, 10])
    assert axis.xaxis.get_major_formatter()(-180, 0) == "-180°"
    assert axis.xaxis.get_major_formatter()(180, 0) == "180°"
    assert axis.xaxis.get_major_formatter()(300, 0) == "-60°"
    reference = np.genfromtxt(output, delimiter=",", names=True)
    assert lab.main([*options, "--lv-style", "inferno", "--lv-stretch", "linear",
                     "--lv-no-contours", "--lv-longitude-order", "increasing",
                     "--output", str(tmp_path / "inferno.csv"), "--no-show"]) == 0
    original = np.genfromtxt(tmp_path / "inferno.csv", delimiter=",", names=True)
    for name in reference.dtype.names:
        np.testing.assert_allclose(reference[name], original[name])
    other = json.loads((tmp_path / "inferno.json").read_text())
    assert other["contour_levels_k"] == []
    assert other["longitude_order"] == "increasing"


@pytest.mark.parametrize("options", [
    ["--lv-asinh-width", "0"], ["--lv-brightness-max", "nan"],
    ["--lv-brightness-min", "10", "--lv-brightness-max", "5"],
    ["--lv-contours", "10", "5"], ["--lv-contours", "0"], ["--lv-contours", "nan"],
])
def test_invalid_lv_appearance_options_fail_before_extraction(options, monkeypatch):
    monkeypatch.setattr(lab, "LABGaussianExtractor", lambda *a: pytest.fail("No cube access"))
    with pytest.raises(SystemExit) as exc:
        lab.main(["--lv", "--lon", "50", "--lat", "0", *options])
    assert exc.value.code == 2
