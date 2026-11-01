from datetime import datetime, timedelta, timezone
import json

import matplotlib
matplotlib.use("Agg")
import numpy as np
import pytest
from astropy.coordinates import SkyCoord
from astropy import units as u
from astropy.io import fits

from models.obs import ObsModel
from models.scan import ScanModel, ScanState, ScanType
from models.target import TargetModel, TargetScanSet
from util import lab_profiler as lab
from obs.obs_pointing import get_obs_pointing


def write_cube(path, nvel=101):
    h = fits.Header()
    h.update(CTYPE1="GLON-CAR", CTYPE2="GLAT-CAR", CTYPE3="VELO-LSR",
             CRVAL1=0., CRPIX1=1., CDELT1=45., CRVAL2=-90., CRPIX2=1.,
             CDELT2=45., CRVAL3=-100000., CRPIX3=1., CDELT3=2000.,
             CUNIT3="m/s", BUNIT="K", BZERO=10., BSCALE=.5, BLANK=-32768)
    data = np.full((nvel, 5, 9), 20, dtype=np.int16)
    data[0] = -32768
    # Duplicate seam must not influence results.
    data[:, :, -1] = 20000
    data[0] = -32768
    fits.PrimaryHDU(data, header=h).writeto(path)
    # astropy resets scaling on PrimaryHDU construction; set it on disk.
    with fits.open(path, mode="update", do_not_scale_image_data=True) as hdul:
        hdul[0].header["BZERO"] = 10.
        hdul[0].header["BSCALE"] = .5
    return path


def test_cube_units_scaling_blanks_seam_and_wrapping(tmp_path):
    path = write_cube(tmp_path / "lab.fit")
    with lab.LABGaussianExtractor(path) as extractor:
        v, t = extractor.extract(359., 0., 60.)
        np.testing.assert_allclose(v[:3], [-100, -98, -96])
        assert np.isnan(t[0])
        np.testing.assert_allclose(t[1:], 20.)
        np.testing.assert_allclose(extractor.extract(-1., 0., 60.)[1], t)
    assert extractor.data is None


def test_fractional_pointing_changes_weights(tmp_path):
    path = write_cube(tmp_path / "lab.fit")
    with fits.open(path, mode="update", do_not_scale_image_data=True) as hdul:
        hdul[0].data[1:, :, 1] = 200
    with lab.LABGaussianExtractor(path) as extractor:
        t0 = extractor.extract(0, 0, 60)[1]
        t1 = extractor.extract(10, 0, 60)[1]
        assert np.all(t1[1:] > t0[1:])
        for values in [(0, 91, 5), (0, 0, 0), (np.nan, 0, 5)]:
            with pytest.raises(ValueError):
                extractor.extract(*values)


def test_pair_averaging_reproduces_read_ray():
    v = np.arange(891.)
    t = v**2
    actual = lab.match_channels(v, t)
    np.testing.assert_array_equal(actual[0], v[1:].reshape(445, 2).mean(axis=1))
    np.testing.assert_array_equal(actual[1], t[1:].reshape(445, 2).mean(axis=1))


def test_csv_selects_both_ids_discards_invalid_and_sorts(tmp_path):
    path = tmp_path / "observation.csv"
    lines = ["target_id,freq_scan,velocity_km_s,calibrated_power"]
    for i in reversed(range(20)):
        lines += [f"7,2,{i},{i + 10}", f"7,0,{i},999", f"5,2,{i},999"]
    lines += ["7,2,30,", "7,2,nan,10"]
    path.write_text("\n".join(lines))
    v, p = lab.load_observation(path, "7", "2")
    np.testing.assert_array_equal(v, np.arange(20))
    np.testing.assert_array_equal(p, v + 10)
    with pytest.raises(ValueError, match="Fewer than 10"):
        lab.load_observation(path, "99", "2")


def test_baseline_matches_read_ray_formula():
    v = np.linspace(-100, 100, 101)
    p = 5 + .02 * v + np.exp(-(v / 10)**2)
    corrected, baseline = lab.subtract_baseline(v, p)
    expected = p[0] + (np.interp(50, v, p) - p[0]) * (v - v[0]) / (50 - v[0])
    np.testing.assert_array_equal(baseline, expected)
    np.testing.assert_array_equal(corrected, p - expected)
    with pytest.raises(ValueError, match="span"):
        lab.subtract_baseline(v[:20], p[:20])
    with pytest.raises(ValueError, match="positive peak"):
        lab.subtract_baseline(v, np.ones_like(v))


@pytest.mark.parametrize("baseline_method", ["read-ray", "linear"])
def test_sweep_best_offsets_and_artifacts(tmp_path, monkeypatch, baseline_method):
    # Known candidate at l=0, b=-1, crossing longitude zero from nominal 359.
    velocity = np.arange(-100., 101., 1.)
    def profile(l, b):
        centre = 8 * ((l + 180) % 360 - 180) + 4 * (b + 1)
        return np.exp(-((velocity - centre) / 8)**2) * 20
    class Extractor:
        def __init__(self, path):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def extract(self, l, b, beam):
            return velocity, profile(l, b)
    monkeypatch.setattr(lab, "LABGaussianExtractor", Extractor)
    v, reference = lab.match_channels(velocity, profile(0, -1))
    p = reference + 10 + .01 * v
    path = tmp_path / "obs.csv"
    path.write_text("target_id,freq_scan,velocity_km_s,calibrated_power\n" + "\n".join(f"7,2,{x},{y}" for x, y in zip(v, p)))
    output = tmp_path / "result.csv"
    assert lab.main(["--lon", "359", "--lat", "0", "--beam", "5", "--sweep",
                     "--file", str(path), "--target-id", "7", "--freq-scan", "2",
                     "--lon-range", "[0,2,1]", "--lat-range", "[-1,0,1]",
                     "--output", str(output), "--no-show", "--observation-baseline", baseline_method]) == 0
    meta = json.loads(output.with_suffix(".json").read_text())
    assert meta["best_longitude_deg"] == 0
    assert meta["best_latitude_deg"] == -1
    assert meta["longitude_offset_deg"] == 1
    assert meta["latitude_offset_deg"] == -1
    assert meta["rms"] < 1e-8
    assert meta["observation_baseline"] == baseline_method
    assert meta["target_id"] == "7" and meta["freq_scan"] == "2"
    assert output.with_suffix(".png").read_bytes().startswith(b"\x89PNG")
    rows = np.loadtxt(tmp_path / "result_sweep.csv", delimiter=",", skiprows=1)
    assert rows.shape == (6, 6)
    assert (tmp_path / "result_comparison.csv").exists()


def test_single_profile_real_cube_cli_and_display_only(tmp_path, monkeypatch):
    path = write_cube(tmp_path / "lab.fit")
    options = ["--galactic", "0", "0", "--lab-fits", str(path), "--beam", "60"]
    output = tmp_path / "single.csv"
    assert lab.main([*options, "--output", str(output), "--no-show"]) == 0
    profile = np.loadtxt(output, delimiter=",", skiprows=1)
    assert profile.shape == (101, 2)
    np.testing.assert_allclose(profile[1:, 1], 20)
    import matplotlib.pyplot as plt
    shown = []
    monkeypatch.setattr(plt, "show", lambda: shown.append(True))
    before = set(tmp_path.iterdir())
    assert lab.main([*options, "--display-only", "--output-dir", str(tmp_path)]) == 0
    assert shown == [True]
    assert set(tmp_path.iterdir()) == before


@pytest.mark.parametrize("options", [
    [], ["--lon", "0"], ["--lon", "0", "--lat", "91"],
    ["--galactic", "0", "0", "--beam", "nan"],
    ["--galactic", "0", "0", "--sweep"],
    ["--galactic", "0", "0", "--file", "obs.csv", "--target-id", "5"],
    ["--galactic", "0", "0", "--display-only", "--no-show"],
    ["--galactic", "0", "0", "--vmin", "10", "--vmax", "0"],
])
def test_invalid_options_do_not_open_cube(options, monkeypatch):
    monkeypatch.setattr(lab, "LABGaussianExtractor", lambda *a: pytest.fail("No cube access"))
    with pytest.raises(SystemExit) as exc:
        lab.main(options)
    assert exc.value.code == 2


@pytest.mark.parametrize("value", ["[0,1,0]", "[1,0,1]", "[nan,1,1]", "bad", "[1,2]"])
def test_invalid_ranges(value):
    with pytest.raises(ValueError):
        lab.range_values(value)


def test_pointing_from_metadata_uses_actual_altaz_and_midpoint(tmp_path):
    start = datetime(2026, 10, 24, 11, 25, tzinfo=timezone.utc)
    scan = ScanModel(obs_id="test", tgt_idx=0, freq_scan=0, scan_iter=0, dig_id="dig003",
                     scan_type=ScanType.SKY, status=ScanState.COMPLETE,
                     read_start=start, read_end=start + timedelta(seconds=20),
                     tgt_acq_altaz={"alt": 18., "az": 48.})
    obs = ObsModel(obs_id="test", latitude=53.234123, longitude=-2.305485, height=77.,
                   targets=[TargetModel(tgt_idx=0, altaz={"alt": 90., "az": 0.})],
                   target_scans=[TargetScanSet(tgt_idx=0, scans=[scan])])
    obs.save_to_disk(output_dir=str(tmp_path))
    galactic, midpoint = get_obs_pointing(tmp_path, "test", scan.scan_id)
    assert midpoint == start + timedelta(seconds=10)
    assert 81 < galactic.l.deg < 84
    assert -7 < galactic.b.deg < -4
    with pytest.raises(ValueError, match="Available scans"):
        get_obs_pointing(tmp_path, "test", "missing")

    # Sidereal/Galactic target coordinates take precedence over fixed Alt/Az.
    obs.targets[0].sky_coord = SkyCoord(l=100*u.deg, b=10*u.deg, frame="galactic").icrs
    obs.save_to_disk(output_dir=str(tmp_path))
    galactic, _ = get_obs_pointing(tmp_path, "test", scan.scan_id)
    assert galactic.l.deg == pytest.approx(100)
    assert galactic.b.deg == pytest.approx(10)

    # The existing metadata CLI still selects the same scan and keeps its name.
    args = lab.build_parser().parse_args(["--obs", "test", "--scan", scan.scan_id, "--dir", str(tmp_path)])
    context = lab.resolve_profile_context(args)
    assert context.input_mode == "metadata"
    assert context.output_name == scan.scan_id
    assert context.observed_at == midpoint
    assert context.location == (53.234123, -2.305485, 77.)


def test_direct_defaults_use_jodrell_and_one_current_utc_time(monkeypatch):
    monkeypatch.setattr("obs.obs_pointing.get_obs_pointing", lambda *a: pytest.fail("Metadata must not be read"))
    args = lab.build_parser().parse_args(["--galactic", "442.2361", "-5.6143"])
    now = datetime(2026, 10, 25, 12, 30, tzinfo=timezone.utc)
    context = lab.resolve_profile_context(args, now=now)
    assert context.observed_at == now
    assert context.location == lab.JODRELL_BANK_LOCATION
    assert context.galactic.l.deg == pytest.approx(82.2361)
    assert context.galactic.b.deg == pytest.approx(-5.6143)
    assert "20261025T123000" in context.output_name
    assert context.obs_id is None and context.scan_id is None


@pytest.mark.parametrize("value", ["2026-10-24T11:25:50Z", "2026-10-24T11:25:50", "2026-10-24T12:25:50+01:00"])
def test_dt_is_normalised_to_utc(value):
    args = lab.build_parser().parse_args(["--galactic", "82", "-5", "--dt", value,
                                         "--location", "45", "-70", "100"])
    context = lab.resolve_profile_context(args)
    assert context.observed_at == datetime(2026, 10, 24, 11, 25, 50, tzinfo=timezone.utc)
    assert context.location == (45, -70, 100)


@pytest.mark.parametrize("method", ["none", "constant", "linear"])
def test_baseline_and_scores_match_hi4pi(method):
    from util import hi4pi
    velocity = np.linspace(-180., 180., 721)
    temperature = 20 * np.exp(-((velocity - 10) / 20)**2) + 6 * np.exp(-((velocity + 50) / 12)**2)
    power = temperature + 12 - .03 * velocity + .04 * np.sin(velocity)
    ref_v = np.linspace(-140., 120., 261)
    ref_t = 20 * np.exp(-((ref_v - 12) / 20)**2) + 6 * np.exp(-((ref_v + 50) / 12)**2)
    ref_t[40:43] = np.nan
    reference = np.interp(velocity, ref_v, ref_t, left=np.nan, right=np.nan)
    mask = np.isfinite(reference) & (velocity >= -100) & (velocity <= 90)
    corrected, baseline = lab.subtract_baseline(velocity, power, method, mask)
    rms, corr = lab.compare_spectra(velocity, corrected, ref_v, ref_t, -100, 90)
    expected = hi4pi.compare_spectra(velocity, power, ref_v, ref_t, -100, 90, method)
    np.testing.assert_allclose([rms, corr], expected[:2], rtol=1e-12, atol=1e-12)
    np.testing.assert_allclose(corrected[mask] / max(corrected[mask]), expected[3])
    np.testing.assert_array_equal(corrected, power - baseline)
    if method == "none":
        np.testing.assert_array_equal(baseline, np.zeros_like(power))


def test_linear_baseline_does_not_require_plus50_and_ignores_outside_fit():
    velocity = np.linspace(-120., 40., 161)
    power = 10 + .01 * velocity + 5 * np.exp(-((velocity + 20) / 5)**2)
    mask = (velocity >= -80) & (velocity <= 30)
    corrected, baseline = lab.subtract_baseline(velocity, power, "linear", mask)
    changed = power.copy()
    changed[~mask] = -1e6
    _, other_baseline = lab.subtract_baseline(velocity, changed, "linear", mask)
    np.testing.assert_array_equal(baseline, other_baseline)
    assert np.max(corrected) > 0
    assert lab.build_parser().parse_args([]).observation_baseline == "linear"


def test_linear_comparison_exports_the_scored_baseline(tmp_path):
    path = write_cube(tmp_path / "lab.fit")
    velocity = np.arange(-100., 102., 2.)
    power = 12 - .02 * velocity + 20 * np.exp(-((velocity - 10) / 20)**2)
    observation = tmp_path / "obs.csv"
    observation.write_text("target_id,freq_scan,velocity_km_s,calibrated_power\n" +
                           "\n".join(f"3,0,{v},{p}" for v, p in zip(velocity, power)))
    output = tmp_path / "fit.csv"
    assert lab.main(["--lon", "0", "--lat", "0", "--lab-fits", str(path),
                     "--file", str(observation), "--target-id", "3", "--freq-scan", "0",
                     "--fit-vmin", "-80", "--fit-vmax", "80",
                     "--output", str(output), "--no-show"]) == 0
    metadata = json.loads(output.with_suffix(".json").read_text())
    assert metadata["observation_baseline"] == "linear"
    assert "20%" in metadata["baseline_method"]
    assert "baseline_reference_velocity_kms" not in metadata
    data = np.loadtxt(tmp_path / "fit_comparison.csv", delimiter=",", skiprows=1)
    reference = data[:, 4]
    mask = np.isfinite(reference) & (velocity >= -80) & (velocity <= 80)
    corrected, baseline = lab.subtract_baseline(velocity, power, "linear", mask)
    np.testing.assert_allclose(data[:, 1], baseline)
    np.testing.assert_allclose(data[:, 2], corrected)
    np.testing.assert_allclose(data[:, 3], corrected * metadata["observation_peak_scale"])


def terminal_test_spectrum():
    velocity = np.arange(-40., 101.)
    brightness = np.zeros_like(velocity)
    # Exactly known line-free noise floor of 2 K and RMS 1 K.
    noise = (velocity >= -40) & (velocity <= -21)
    brightness[noise] = np.tile([1., 3.], 10)
    brightness[(velocity >= 10) & (velocity <= 50)] = 12.
    brightness[velocity == 51] = 8.
    brightness[velocity == 52] = 4.
    brightness[velocity == 90] = 9.  # Isolated high-velocity noise spike.
    return velocity, brightness


def test_terminal_interpolates_rightmost_sustained_crossing():
    velocity, brightness = terminal_test_spectrum()
    result = lab.estimate_terminal_velocity(velocity, brightness, -100, 100, [[-40, -21]])
    assert result["status"] == "detected"
    assert result["noise_level_k"] == 2
    assert result["noise_rms_k"] == 1
    assert result["threshold_k"] == 5
    assert result["velocity_lsr_kms"] == pytest.approx(51.75)
    literal = lab.estimate_terminal_velocity(velocity, brightness, -100, 100, [[-40, -21]], min_channels=1)
    assert literal["velocity_lsr_kms"] == pytest.approx(90 + 4 / 9)
    # A negative-velocity feature cannot be reported as terminal positive emission.
    brightness[velocity < -10] += 100
    assert lab.estimate_terminal_velocity(velocity, brightness, 0, 100, [[-40, -21]])["status"] == "not_detected"


def test_terminal_is_invariant_under_plot_scaling():
    velocity, brightness = terminal_test_spectrum()
    reference = lab.estimate_terminal_velocity(velocity, brightness, 0, 100, [[-40, -21]])
    scaled = lab.estimate_terminal_velocity(velocity, brightness * 7, 0, 100, [[-40, -21]])
    assert scaled["velocity_lsr_kms"] == reference["velocity_lsr_kms"]
    assert scaled["noise_rms_k"] == 7 * reference["noise_rms_k"]


@pytest.mark.parametrize("case,status", [("no emission", "not_detected"), ("edge", "edge_limited"),
                                        ("gap", "gap_limited"), ("short", "insufficient_noise_channels"),
                                        ("constant", "zero_noise_rms")])
def test_terminal_failure_states(case, status):
    velocity, brightness = terminal_test_spectrum()
    window = [[-40, -21]]
    if case == "no emission":
        brightness[velocity >= 0] = 0
    elif case == "edge":
        brightness[velocity >= 10] = 12
    elif case == "gap":
        brightness[velocity == 52] = np.nan
    elif case == "short":
        window = [[-40, -38]]
    elif case == "constant":
        brightness[velocity < 0] = 2
    result = lab.estimate_terminal_velocity(velocity, brightness, 0, 100, window)
    assert result["status"] == status
    assert result["velocity_lsr_kms"] is None


def test_terminal_does_not_bridge_missing_channels():
    velocity, brightness = terminal_test_spectrum()
    brightness[velocity >= 0] = 0
    brightness[np.isin(velocity, [70, 72, 74])] = 12
    keep = ~np.isin(velocity, [71, 73])
    result = lab.estimate_terminal_velocity(velocity[keep], brightness[keep], 0, 100, [[-40, -21]])
    assert result["status"] == "not_detected"


def test_terminal_automatic_noise_window_and_repeated_ranges():
    velocity, brightness = terminal_test_spectrum()
    brightness[velocity >= 80] = np.resize([1., 3.], (velocity >= 80).sum())
    result = lab.estimate_terminal_velocity(velocity, brightness, 0, 100)
    assert result["status"] == "detected"
    assert result["noise_sample_count"] == 20
    assert result["noise_ranges_kms"] == [[81., 100.]]
    explicit = lab.estimate_terminal_velocity(velocity, brightness, 0, 100, [[-40, -31], [-30, -21]])
    assert explicit["noise_sample_count"] == 20
    assert explicit["noise_rms_k"] == 1


def test_terminal_plot_and_metadata_use_observation(tmp_path, monkeypatch):
    import matplotlib.pyplot as plt
    velocity, brightness = terminal_test_spectrum()
    # Avoid scaling the synthetic observation: match the survey peak to it.
    result = lab.FitResult(50, 0, 0, 0, .1, .9, velocity, brightness)
    args = lab.build_parser().parse_args(["--lon", "50", "--lat", "0", "--file", "obs.csv",
                                         "--target-id", "3", "--freq-scan", "0",
                                         "--noise-range", "-40", "-21", "--output", str(tmp_path / "terminal.csv")])
    args.galactic = [50, 0]
    context = lab.resolve_profile_context(args)
    captured = []
    monkeypatch.setattr(plt, "show", lambda: captured.extend(plt.gcf().axes[0].get_lines()))
    lab.plot_result(result, args, context, (velocity, brightness, np.zeros_like(brightness)))
    metadata = json.loads((tmp_path / "terminal.json").read_text())
    terminal = metadata["terminal_velocity"]
    assert terminal["source"] == "observation"
    assert terminal["velocity_lsr_kms"] == pytest.approx(51.75)
    assert any("+51.75 km/s" in line.get_label() for line in captured)
    assert any(line.get_color() == "red" and "Observation" in line.get_label() for line in captured)
    assert any(line.get_color() == "black" and line.get_linestyle() == "--" for line in captured)


@pytest.mark.parametrize("options", [
    ["--terminal-sigma", "0"], ["--terminal-sigma", "nan"],
    ["--terminal-min-channels", "0"], ["--noise-range", "100", "50"],
    ["--noise-range", "nan", "50"], ["--terminal-source", "observation"],
])
def test_invalid_terminal_options_fail_before_cube_access(options, monkeypatch):
    monkeypatch.setattr(lab, "LABGaussianExtractor", lambda *a: pytest.fail("No cube access"))
    with pytest.raises(SystemExit) as exc:
        lab.main(["--lon", "50", "--lat", "0", *options])
    assert exc.value.code == 2
