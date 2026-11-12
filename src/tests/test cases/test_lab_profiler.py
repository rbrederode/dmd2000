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


def test_pair_averaging_drops_leading_odd_channel():
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


def test_baseline_defaults_to_linear():
    # This spectrum does not reach +50 km/s; a linear fit needs no fixed anchor.
    v = np.linspace(-100, 40, 101)
    expected = 5 + .02 * v
    p = expected + np.exp(-((v + 30) / 5)**2)
    corrected, baseline = lab.subtract_baseline(v, p)
    np.testing.assert_allclose(baseline, expected, atol=1e-10)
    np.testing.assert_allclose(corrected, p - expected, atol=1e-10)
    explicit = lab.subtract_baseline(v, p, method="linear")
    np.testing.assert_array_equal(baseline, explicit[1])
    with pytest.raises(ValueError, match="positive peak"):
        lab.subtract_baseline(v, np.zeros_like(v))
    assert lab.build_parser().parse_args(["--noise-estimator", "mean_rms", "--noise-selection", "positive_edge"]).observation_baseline == "linear"


def test_sweep_best_offsets_and_artifacts(tmp_path, monkeypatch):
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
                     "--output", str(output), "--no-show", "--noise-estimator", "mean_rms", "--noise-selection", "positive_edge"]) == 0
    meta = json.loads(output.with_suffix(".json").read_text())
    assert meta["best_longitude_deg"] == 0
    assert meta["best_latitude_deg"] == -1
    assert meta["longitude_offset_deg"] == 1
    assert meta["latitude_offset_deg"] == -1
    assert meta["rms"] < 1e-8
    assert meta["observation_baseline"] == "linear"
    assert meta["target_id"] == "7" and meta["freq_scan"] == "2"
    assert output.with_suffix(".png").read_bytes().startswith(b"\x89PNG")
    rows = np.loadtxt(tmp_path / "result_sweep.csv", delimiter=",", skiprows=1)
    assert rows.shape == (6, 6)
    assert (tmp_path / "result_comparison.csv").exists()


def test_single_profile_real_cube_cli_and_display_only(tmp_path, monkeypatch):
    path = write_cube(tmp_path / "lab.fit")
    options = ["--galactic", "0", "0", "--lab-fits", str(path), "--beam", "60"]
    output = tmp_path / "single.csv"
    assert lab.main([*options, "--output", str(output), "--no-show", "--noise-estimator", "mean_rms", "--noise-selection", "positive_edge"]) == 0
    profile = np.loadtxt(output, delimiter=",", skiprows=1)
    assert profile.shape == (101, 2)
    np.testing.assert_allclose(profile[1:, 1], 20)
    import matplotlib.pyplot as plt
    shown = []
    monkeypatch.setattr(plt, "show", lambda: shown.append(True))
    before = set(tmp_path.iterdir())
    assert lab.main([*options, "--display-only", "--output-dir", str(tmp_path), "--noise-estimator", "mean_rms", "--noise-selection", "positive_edge"]) == 0
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
    args = lab.build_parser().parse_args(["--obs", "test", "--scan", scan.scan_id, "--dir", str(tmp_path), "--noise-estimator", "mean_rms", "--noise-selection", "positive_edge"])
    context = lab.resolve_profile_context(args)
    assert context.input_mode == "metadata"
    assert context.output_name == scan.scan_id
    assert context.observed_at == midpoint
    assert context.location == (53.234123, -2.305485, 77.)


def test_direct_defaults_use_jodrell_and_one_current_utc_time(monkeypatch):
    monkeypatch.setattr("obs.obs_pointing.get_obs_pointing", lambda *a: pytest.fail("Metadata must not be read"))
    args = lab.build_parser().parse_args(["--galactic", "442.2361", "-5.6143", "--noise-estimator", "mean_rms", "--noise-selection", "positive_edge"])
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
                                         "--location", "45", "-70", "100", "--noise-estimator", "mean_rms", "--noise-selection", "positive_edge"])
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
    assert lab.build_parser().parse_args(["--noise-estimator", "mean_rms", "--noise-selection", "positive_edge"]).observation_baseline == "linear"


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
                     "--output", str(output), "--no-show", "--no-baseline-auto", "--noise-estimator", "mean_rms", "--noise-selection", "positive_edge"]) == 0
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


def test_automatic_baseline_windows_anchor_both_ends_without_fitting_emission():
    v=np.arange(-300.,301.)
    expected=10+.002*v
    line=4*np.exp(-.5*(v/15)**2)
    power=expected+line+.01*np.where(np.arange(len(v))%2,1,-1)
    windows=lab.select_baseline_windows(v[::-1],power[::-1],-250,250)
    assert windows == [[-250,-201],[201,250]]
    corrected,baseline=lab.subtract_baseline(v,power,"linear",baseline_windows=windows)
    np.testing.assert_allclose(baseline,expected,atol=.002)
    assert corrected[v==0][0] == pytest.approx(3.99,abs=.002)


def test_automatic_baseline_selection_excludes_invalid_and_masked_channels():
    v=np.arange(-250.,251.)
    power=np.ma.array(np.ones_like(v),mask=np.abs(v)==250)
    power[np.abs(v)==249]=np.nan
    windows=lab.select_baseline_windows(v,power,-250,250)
    assert windows[0][0] == -248
    assert windows[1][1] == 248


@pytest.mark.parametrize("limits", [(0,250),(-250,0),(-10,10)])
def test_automatic_baseline_requires_two_sufficiently_sampled_sides(limits):
    v=np.arange(-250.,251.)
    with pytest.raises(ValueError):
        lab.select_baseline_windows(v,np.ones_like(v),*limits)


@pytest.mark.parametrize("mode", ["default", "manual", "legacy"])
def test_baseline_cli_default_and_overrides_record_actual_windows(tmp_path,mode):
    v=np.arange(-250.,251.)
    power=10+.002*v+4*np.exp(-.5*(v/15)**2)
    csv_path=tmp_path/"observation.csv"
    np.savetxt(csv_path,np.column_stack([np.full(len(v),3),np.zeros(len(v)),v,power]),
               delimiter=",",header="target_id,freq_scan,velocity_km_s,calibrated_power",comments="",
               fmt=["%d","%d","%.9f","%.9f"])
    cube=write_cube(tmp_path/"flat.fit")
    options={"default":[],"manual":["--baseline-window","-240","-190","--baseline-window","170","240"],
             "legacy":["--no-baseline-auto"]}[mode]
    assert lab.main(["--galactic","0","0","--lab-fits",str(cube),"--file",str(csv_path),
        "--target-id","3","--freq-scan","0","--vmin","-250","--vmax","250",
        "--no-terminal-velocity","--no-show","--output",str(tmp_path/"auto_baseline"),*options, "--noise-estimator", "mean_rms", "--noise-selection", "positive_edge"]) == 0
    metadata=json.loads((tmp_path/"auto_baseline.json").read_text())
    assert metadata["baseline_window_selection"] == {"default":"automatic_outer_20_percent_per_side",
        "manual":"explicit","legacy":"method_default"}[mode]
    assert metadata["baseline_windows_kms"] == {"default":[[-250,-201],[201,250]],
        "manual":[[-240,-190],[170,240]],"legacy":None}[mode]
    comparison=np.loadtxt(tmp_path/"auto_baseline_comparison.csv",delimiter=",",skiprows=1)
    if mode != "legacy":
        np.testing.assert_allclose(comparison[:,1],10+.002*comparison[:,0],atol=1e-7)


@pytest.mark.parametrize("options", [
    ["--baseline-auto","--vmin","0"],
    ["--baseline-auto","--vmax","0"],
])
def test_invalid_automatic_baseline_options_fail_before_cube_access(options,monkeypatch):
    monkeypatch.setattr(lab,"LABGaussianExtractor",lambda *a:pytest.fail("No cube access"))
    with pytest.raises(SystemExit) as exc:
        lab.main(["--galactic","35","0","--file","obs.csv","--target-id","3","--freq-scan","0",*options, "--noise-estimator", "mean_rms", "--noise-selection", "positive_edge"])
    assert exc.value.code == 2


@pytest.mark.parametrize("method", ["connected","threshold","erfc","disabled"])
def test_plot_annotates_baseline_and_applied_noise_windows(monkeypatch,method):
    import matplotlib.pyplot as plt
    v,t=terminal_test_spectrum()
    options=["--galactic","50","0","--file","obs.csv","--target-id","3","--freq-scan","0",
             "--no-baseline-auto","--baseline-window","-40","-21","--baseline-window","70","100",
             "--noise-range","-40","-21","--display-only"]
    options+=(["--no-terminal-velocity"] if method=="disabled" else ["--terminal-method",method])
    if method=="erfc":
        options += ["--terminal-fit-range","10","90"]
    args=lab.build_parser().parse_args(options)
    captured=[]
    monkeypatch.setattr(plt,"show",lambda:captured.extend(text.get_text() for text in plt.gcf().axes[0].texts))
    lab.plot_result(lab.FitResult(50,0,0,0,.1,.9,v,t),args,lab.resolve_profile_context(args),
                    (v,t,np.zeros_like(t)))
    text="\n".join(captured)
    assert "Baseline (manual):" in text
    assert "-40.0 to -21.0; 70.0 to 100.0 km/s" in text
    if method in ("disabled","erfc"):
        assert "Noise threshold: " in text
    elif method=="connected":
        assert "Noise (observation, shared):" in text
    else:
        assert "Noise (lab, observation):" in text


def test_explicit_baseline_windows_are_independent_of_scoring_mask():
    v = np.arange(-200., 201.)
    line = 4*np.exp(-.5*(v/10)**2)
    expected = 10 + .01*v
    noise = .01*np.where(np.arange(len(v)) % 2, 1, -1)
    corrected, baseline = lab.subtract_baseline(v, expected+line+noise, "linear",
                                               (v >= -10) & (v <= 10),
                                               baseline_windows=[[-200,-100],[100,200]])
    np.testing.assert_allclose(baseline, expected, atol=.002)
    assert corrected[v == 0][0] == pytest.approx(4+noise[v == 0][0], abs=.002)


@pytest.mark.parametrize("options", [
    ["--baseline-window", "100", "50"],
    ["--baseline-window", "nan", "50"],
    ["--baseline-window", "-200", "-100", "--observation-baseline", "none"],
])
def test_invalid_baseline_windows_fail_before_cube_access(options, monkeypatch):
    monkeypatch.setattr(lab, "LABGaussianExtractor", lambda *a: pytest.fail("No cube access"))
    with pytest.raises(SystemExit) as exc:
        lab.main(["--galactic", "35", "0", "--file", "obs.csv", "--target-id", "4", "--freq-scan", "0", *options, "--noise-estimator", "mean_rms", "--noise-selection", "positive_edge"])
    assert exc.value.code == 2


def test_terminal_interpolates_rightmost_sustained_crossing():
    velocity, brightness = terminal_test_spectrum()
    result = lab.estimate_terminal_velocity(velocity, brightness, -100, 100, [[-40, -21]], noise_estimator="mean_rms", noise_selection="positive_edge")
    assert result["status"] == "detected"
    assert result["noise_level_k"] == 2
    assert result["noise_rms_k"] == 1
    assert result["threshold_k"] == 5
    assert result["velocity_lsr_kms"] == pytest.approx(51.75)
    literal = lab.estimate_terminal_velocity(velocity, brightness, -100, 100, [[-40, -21]], min_channels=1, noise_estimator="mean_rms", noise_selection="positive_edge")
    assert literal["velocity_lsr_kms"] == pytest.approx(90 + 4 / 9)
    # A negative-velocity feature cannot be reported as terminal positive emission.
    brightness[velocity < -10] += 100
    assert lab.estimate_terminal_velocity(velocity, brightness, 0, 100, [[-40, -21]], noise_estimator="mean_rms", noise_selection="positive_edge")["status"] == "not_detected"


def test_connected_edge_ignores_detached_emission_and_bridges_short_dips():
    v, t = terminal_test_spectrum()
    t[(v>=70)&(v<=76)] = 9  # Sustained but detached island: old estimator selects it.
    t[v==35] = 0  # A short dip within the main emission is not its end.
    old = lab.estimate_terminal_velocity(v,t,-100,100,[[-40,-21]], noise_estimator="mean_rms", noise_selection="positive_edge")
    edge = lab.estimate_connected_terminal_velocity(v,t,-100,100,[[-40,-21]], noise_estimator="mean_rms", noise_selection="positive_edge")
    assert old["velocity_lsr_kms"] > 70
    assert edge["status"] == "detected"
    assert edge["velocity_lsr_kms"] == pytest.approx(51.75)
    assert edge["return_confirmed_width_kms"] >= 3
    assert edge["return_below_threshold_channels"] >= 3
    scaled = lab.estimate_connected_terminal_velocity(v[::-1],7*t[::-1],-100,100,[[-40,-21]], noise_estimator="mean_rms", noise_selection="positive_edge")
    assert scaled["velocity_lsr_kms"] == pytest.approx(edge["velocity_lsr_kms"])


@pytest.mark.parametrize("case,status", [("gap","gap_limited"),("coverage","edge_limited"),
                                         ("no_emission","not_detected"),("no_noise","zero_noise_rms")])
def test_connected_edge_does_not_claim_unconfirmed_crossings(case,status):
    v,t = terminal_test_spectrum()
    vmax=100
    if case=="gap":
        t[v==53]=np.nan
    elif case=="coverage":
        vmax=54  # Below-threshold run has not spanned 3 km/s yet.
    elif case=="no_emission":
        t[v>0]=0
    else:
        t[(v>=-40)&(v<=-21)]=2
    edge=lab.estimate_connected_terminal_velocity(v,t,-100,vmax,[[-40,-21]], noise_estimator="mean_rms", noise_selection="positive_edge")
    assert edge["status"] == status
    assert edge["velocity_lsr_kms"] is None


def test_connected_comparison_uses_one_observation_threshold(tmp_path):
    v,survey=terminal_test_spectrum()
    observed=survey.copy()
    observed[(v>=-40)&(v<=-21)]=np.tile([0.,4.],10)  # Threshold 8 instead of LAB's own 5.
    observed[(v>=70)&(v<=76)]=9
    reference=lab.estimate_terminal_velocity(v,observed,-100,100,[[-40,-21]], noise_estimator="mean_rms", noise_selection="positive_edge")
    edge=lab.estimate_connected_terminal_velocity(v,survey,-100,100,[[-40,-21]],
                                                threshold_reference=reference,threshold_source="observation", noise_estimator="mean_rms", noise_selection="positive_edge")
    assert edge["threshold_k"] == 8
    assert edge["velocity_lsr_kms"] == pytest.approx(51)
    assert edge["threshold_source"] == "observation"
    result=lab.FitResult(50,0,0,0,.1,.9,v,survey)
    args=lab.build_parser().parse_args(["--galactic","50","0","--file","obs.csv","--target-id","3",
        "--freq-scan","0","--terminal-method","connected","--noise-range","-40","-21",
        "--no-show","--output",str(tmp_path/"connected"), "--noise-estimator", "mean_rms", "--noise-selection", "positive_edge"])
    lab.plot_result(result,args,lab.resolve_profile_context(args),(v,observed,np.zeros_like(v)))
    terminal=json.loads((tmp_path/"connected.json").read_text())["terminal_velocity"]
    assert terminal["method"] == "connected"
    assert terminal["observation_minus_lab_kms"] == pytest.approx(0)
    for estimate in terminal["spectra"].values():
        assert estimate["status"] == "detected"
        assert estimate["threshold_k"] == 8
        assert estimate["threshold_source"] == "observation"


def test_connected_invalid_shared_noise_does_not_fall_back_to_lab_noise():
    v,t=terminal_test_spectrum()
    invalid=lab.estimate_terminal_velocity(v,np.ones_like(t),-100,100,[[-40,-21]], noise_estimator="mean_rms", noise_selection="positive_edge")
    result=lab.estimate_connected_terminal_velocity(v,t,-100,100,[[-40,-21]],
                                                  threshold_reference=invalid,threshold_source="observation", noise_estimator="mean_rms", noise_selection="positive_edge")
    assert result["status"] == "invalid_threshold_reference"
    assert result["velocity_lsr_kms"] is None


def test_scaled_observation_snr_has_explicit_db_reference_and_preserves_negative_floor():
    v,t=terminal_test_spectrum()
    for y in (t,7*t,t-4):
        stats=lab.observation_noise_metrics(v,y,-100,100,[[-40,-21]], noise_estimator="mean_rms", noise_selection="positive_edge")
        assert stats["status"] == "measured"
        assert stats["peak_snr"] == pytest.approx(10)
        assert stats["peak_snr_db"] == pytest.approx(10)
        assert stats["noise_rms_db_relative_to_peak"] == pytest.approx(-10)
        assert stats["noise_ranges_kms"] == [[-40,-21]]
    assert stats["noise_level_k"] == -2  # A mean floor need not have a defined logarithm.


@pytest.mark.parametrize("case,status",[("zero","zero_noise_rms"),("few","insufficient_noise_channels"),
                                      ("negative_peak","no_positive_peak")])
def test_observation_snr_unavailable_values_are_json_safe(case,status):
    v,t=terminal_test_spectrum()
    windows=[[-40,-21]]
    if case=="zero":
        t[(v>=-40)&(v<=-21)]=2
    elif case=="few":
        windows=[[-40,-38]]
    else:
        t[v>=0]=-10
    stats=lab.observation_noise_metrics(v,t,0,100,windows, noise_estimator="mean_rms", noise_selection="positive_edge")
    assert stats["status"] == status
    assert stats["peak_snr_db"] is None
    assert stats["noise_rms_db_relative_to_peak"] is None
    json.dumps(stats,allow_nan=False)


def test_terminal_is_invariant_under_plot_scaling():
    velocity, brightness = terminal_test_spectrum()
    reference = lab.estimate_terminal_velocity(velocity, brightness, 0, 100, [[-40, -21]], noise_estimator="mean_rms", noise_selection="positive_edge")
    scaled = lab.estimate_terminal_velocity(velocity, brightness * 7, 0, 100, [[-40, -21]], noise_estimator="mean_rms", noise_selection="positive_edge")
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
    result = lab.estimate_terminal_velocity(velocity, brightness, 0, 100, window, noise_estimator="mean_rms", noise_selection="positive_edge")
    assert result["status"] == status
    assert result["velocity_lsr_kms"] is None


def test_terminal_does_not_bridge_missing_channels():
    velocity, brightness = terminal_test_spectrum()
    brightness[velocity >= 0] = 0
    brightness[np.isin(velocity, [70, 72, 74])] = 12
    keep = ~np.isin(velocity, [71, 73])
    result = lab.estimate_terminal_velocity(velocity[keep], brightness[keep], 0, 100, [[-40, -21]], noise_estimator="mean_rms", noise_selection="positive_edge")
    assert result["status"] == "not_detected"


def test_terminal_automatic_noise_window_and_repeated_ranges():
    velocity, brightness = terminal_test_spectrum()
    brightness[velocity >= 80] = np.resize([1., 3.], (velocity >= 80).sum())
    result = lab.estimate_terminal_velocity(velocity, brightness, 0, 100, noise_estimator="mean_rms", noise_selection="positive_edge")
    assert result["status"] == "detected"
    assert result["noise_sample_count"] == 20
    assert result["noise_ranges_kms"] == [[81., 100.]]
    explicit = lab.estimate_terminal_velocity(velocity, brightness, 0, 100, [[-40, -31], [-30, -21]], noise_estimator="mean_rms", noise_selection="positive_edge")
    assert explicit["noise_sample_count"] == 20
    assert explicit["noise_rms_k"] == 1


def test_terminal_plot_and_metadata_use_observation(tmp_path, monkeypatch):
    import matplotlib.pyplot as plt
    velocity, brightness = terminal_test_spectrum()
    # Avoid scaling the synthetic observation: match the survey peak to it.
    result = lab.FitResult(50, 0, 0, 0, .1, .9, velocity, brightness)
    args = lab.build_parser().parse_args(["--lon", "50", "--lat", "0", "--file", "obs.csv",
                                         "--target-id", "3", "--freq-scan", "0",
                                         "--noise-range", "-40", "-21", "--output", str(tmp_path / "terminal.csv"), "--noise-estimator", "mean_rms", "--noise-selection", "positive_edge"])
    args.galactic = [50, 0]
    context = lab.resolve_profile_context(args)
    captured = []
    monkeypatch.setattr(plt, "show", lambda: captured.extend(plt.gcf().axes[0].get_lines()))
    lab.plot_result(result, args, context, (velocity, brightness, np.zeros_like(brightness)))
    metadata = json.loads((tmp_path / "terminal.json").read_text())
    terminal = metadata["terminal_velocity"]
    assert terminal["source"] == "observation"
    assert terminal["velocity_lsr_kms"] == pytest.approx(51.75)
    assert terminal["spectra"]["lab"]["velocity_lsr_kms"] == pytest.approx(51.75)
    assert terminal["observation_minus_lab_kms"] == pytest.approx(0)
    assert metadata["observation_noise"]["peak_snr"] == pytest.approx(10)
    assert metadata["observation_noise"]["noise_rms_db_relative_to_peak"] == pytest.approx(-10)
    assert any("+51.75 km/s" in line.get_label() for line in captured)
    assert any(line.get_color() == "red" and "Observation" in line.get_label() for line in captured)
    assert any(line.get_color() == "black" and line.get_linestyle() == "--" for line in captured)


@pytest.mark.parametrize("mode", ["auto", "lab", "observation", "disabled", "lab_no_detection"])
def test_threshold_comparison_sources_difference_and_missing_edge(tmp_path, monkeypatch, mode):
    import matplotlib.pyplot as plt
    v, survey = terminal_test_spectrum()
    observed = survey.copy()
    # Observation edge is shifted +5 km/s; its independent noise threshold is 8 K.
    observed[v > 0] = np.interp(v[v > 0] - 5, v, survey)
    observed[(v >= -40) & (v <= -21)] = np.tile([0., 4.], 10)
    if mode == "lab_no_detection":
        survey[v > 0] = 0
        survey[v == -10] = 12  # Keep the peak scaling unchanged.
    result = lab.FitResult(50, 0, 0, 0, .1, .9, v, survey)
    options = ["--lon", "50", "--lat", "0", "--file", "obs.csv", "--target-id", "3",
               "--freq-scan", "0", "--noise-range", "-40", "-21", "--output", str(tmp_path / "edge"),
               "--noise-estimator", "mean_rms", "--noise-selection", "positive_edge"]
    if mode in ("lab", "observation"):
        options += ["--terminal-source", mode]
    elif mode == "disabled":
        options += ["--no-terminal-velocity"]
    args = lab.build_parser().parse_args(options)
    args.galactic = [50, 0]
    captured = {}
    def capture():
        ax = plt.gcf().axes[0]
        captured.update(title=ax.get_title(), labels=[line.get_label() for line in ax.get_lines()],
                        lines=ax.get_lines())
    monkeypatch.setattr(plt, "show", capture)
    lab.plot_result(result, args, lab.resolve_profile_context(args), (v, observed, np.zeros_like(v)))
    terminal = json.loads((tmp_path / "edge.json").read_text())["terminal_velocity"]
    if mode == "disabled":
        assert terminal == {"status": "disabled"}
        assert "Δv_T" not in captured["title"]
        return
    expected_sources = {mode} if mode in ("lab", "observation") else {"lab", "observation"}
    assert set(terminal["spectra"]) == expected_sources
    floor_lines = [line for line in captured["lines"] if "noise floor (" in line.get_label()]
    assert len(floor_lines) == len(expected_sources)
    for line in floor_lines:
        assert line.get_linestyle() == "--"
        np.testing.assert_allclose(line.get_ydata(), [2.,2.])
    if mode == "auto":
        assert terminal["spectra"]["lab"]["threshold_k"] == pytest.approx(5)
        assert terminal["spectra"]["observation"]["threshold_k"] == pytest.approx(8)
        assert terminal["spectra"]["lab"]["velocity_lsr_kms"] == pytest.approx(51.75)
        assert terminal["spectra"]["observation"]["velocity_lsr_kms"] == pytest.approx(56)
        assert terminal["observation_minus_lab_kms"] == pytest.approx(4.25)
        assert "Terminal Velocity (Δv_T): +4.25 km/s (observation − LAB)" in captured["title"]
        assert sum("Terminal velocity (" in label for label in captured["labels"]) == 2
    else:
        assert "observation_minus_lab_kms" not in terminal
        if mode == "lab_no_detection":
            assert terminal["spectra"]["lab"]["status"] == "not_detected"
            assert "Terminal Velocity (Δv_T): unavailable (observation − LAB)" in captured["title"]
        else:
            assert "Δv_T" not in captured["title"]


def test_erfc_recovers_known_edge_and_is_scale_invariant():
    v = np.linspace(10, 110, 501)
    rng = np.random.default_rng(24)
    t = lab.erfc_edge(v, .2, 12., 51., 7.) + rng.normal(0, .04, len(v))
    fit = lab.fit_terminal_erfc(v, t, [10, 110])
    scaled = lab.fit_terminal_erfc(v[::-1], 13*t[::-1], [10, 110])
    assert fit["status"] == scaled["status"] == "fitted"
    assert fit["velocity_lsr_kms"] == pytest.approx(51, abs=.1)
    assert fit["width_kms"] == pytest.approx(7, abs=.1)
    assert fit["velocity_formal_error_kms"] > 0
    assert fit["velocity_lsr_kms"] == pytest.approx(scaled["velocity_lsr_kms"], abs=1e-5)


def test_erfc_rejects_gaps_and_incomplete_wings():
    v = np.linspace(10, 110, 501)
    t = lab.erfc_edge(v, 0, 12, 51, 7)
    assert lab.fit_terminal_erfc(v, t, [45, 55])["status"] == "unconstrained_edge"
    t[250] = np.nan
    assert lab.fit_terminal_erfc(v, t, [10, 110])["status"] == "gap_in_fit_window"
    assert lab.fit_terminal_erfc(v[:5], t[:5], [10, 110])["status"] == "insufficient_channels"
    assert lab.fit_terminal_erfc(v, np.ones_like(v), [10, 110])["status"] == "no_falling_edge"


@pytest.mark.parametrize("step", [.3, 2.])
def test_automatic_erfc_recovers_edge_despite_rising_side_and_isolated_spike(step):
    v = np.arange(-100., 151., step)
    rng = np.random.default_rng(77)
    # A much sharper rising side precedes the falling transition of interest.
    rising = 1/(1+np.exp(np.clip(-(v-15)/.3, -700, 700)))
    t = .2 + rising*lab.erfc_edge(v, 0, 12, 51, 7) + rng.normal(0, .025, len(v))
    t[np.argmin(abs(v-110))] += 100
    fit = lab.fit_terminal_erfc(v, t, search_range=[0,150])
    scaled = lab.fit_terminal_erfc(v[::-1], 13*t[::-1], search_range=[0,150])
    assert fit["status"] == scaled["status"] == "fitted"
    assert fit["window_selection"] == "automatic_falling_wing"
    assert fit["velocity_lsr_kms"] == pytest.approx(51, abs=.3)
    assert fit["width_kms"] == pytest.approx(7, abs=.4)
    assert fit["velocity_lsr_kms"] == pytest.approx(scaled["velocity_lsr_kms"], abs=1e-5)
    assert 0 <= fit["fit_range_kms"][0] < 51 < fit["fit_range_kms"][1] <= 150
    assert fit["fit_range_kms"][1] < 110


def test_automatic_erfc_rejects_noise_rising_edges_and_truncated_coverage():
    v = np.linspace(0,150,501)
    rng = np.random.default_rng(33)
    for t in (np.ones_like(v), rng.normal(0,1,len(v)), -lab.erfc_edge(v,0,12,51,7)):
        assert lab.fit_terminal_erfc(v,t,search_range=[0,150])["status"] == "no_falling_edge"
    t = lab.erfc_edge(v,0,12,51,7)
    assert lab.fit_terminal_erfc(v,t,search_range=[45,55])["status"] != "fitted"


def test_automatic_wing_does_not_bridge_missing_channels():
    v = np.linspace(0,150,501)
    t = lab.erfc_edge(v,0,12,51,7)
    t[(v>45)&(v<55)] = np.nan
    fit = lab.fit_terminal_erfc(v,t,search_range=[0,150])
    if "selected_range_kms" in fit:
        low, high = fit["selected_range_kms"]
        assert high <= 45 or low >= 55
    assert fit["status"] != "fitted"


def test_automatic_erfc_cli_exports_selected_windows_and_difference(tmp_path):
    cube=tmp_path/"edge.fit"
    # Exercise main() with a cube exposing a known edge and no manual window.
    v=np.linspace(-100,150,501)
    survey=lab.erfc_edge(v,0,20,50,7)
    observed=lab.erfc_edge(v,.01,.4,55,8)
    csv_path=tmp_path/"observation.csv"
    np.savetxt(csv_path,np.column_stack([np.full(len(v),3),np.zeros(len(v)),v,observed]),
               delimiter=",",header="target_id,freq_scan,velocity_km_s,calibrated_power",comments="",
               fmt=["%d","%d","%.9f","%.9f"])
    # A real small CAR cube with identical spectra at every spatial pixel.
    from astropy.io import fits
    h=fits.Header(dict(CTYPE1="GLON-CAR",CTYPE2="GLAT-CAR",CTYPE3="VELO-LSR",
                      CRVAL1=0.,CRPIX1=1.,CDELT1=90.,CRVAL2=-90.,CRPIX2=1.,CDELT2=90.,
                      CRVAL3=-100000.,CRPIX3=1.,CDELT3=500.,CUNIT3="m/s",BUNIT="K"))
    fits.PrimaryHDU(np.broadcast_to(survey[:,None,None],(len(v),3,5)).copy(),header=h).writeto(cube,overwrite=True)
    assert lab.main(["--galactic","50","0","--lab-fits",str(cube),"--file",str(csv_path),
                     "--target-id","3","--freq-scan","0","--observation-baseline","none",
                     "--terminal-method","erfc","--vmax","150","--no-show",
                     "--output",str(tmp_path/"auto"), "--noise-estimator", "mean_rms", "--noise-selection", "positive_edge"]) == 0
    terminal=json.loads((tmp_path/"auto.json").read_text())["terminal_velocity"]
    for result in terminal["spectra"].values():
        assert result["status"] == "fitted"
        assert result["window_selection"] == "automatic_falling_wing"
        assert result["search_range_kms"] == [0,150]
        assert result["selected_range_kms"] == result["fit_range_kms"]
    assert terminal["observation_minus_lab_kms"] == pytest.approx(5,abs=.1)


def test_erfc_plot_exports_both_native_amplitude_fits(tmp_path):
    v = np.linspace(-100, 150, 501)
    survey = lab.erfc_edge(v, 0, 20, 50, 7)
    observed = lab.erfc_edge(v, .01, .4, 55, 8)
    result = lab.FitResult(50, 0, 0, 0, .1, .9, v, survey)
    args = lab.build_parser().parse_args([
        "--lon", "50", "--lat", "0", "--file", "obs.csv", "--target-id", "3", "--freq-scan", "0",
        "--terminal-method", "erfc", "--terminal-fit-range", "15", "110", "--vmax", "150",
        "--no-show", "--output", str(tmp_path / "edge.csv"), "--noise-estimator", "mean_rms", "--noise-selection", "positive_edge"])
    args.galactic = [50, 0]
    context = lab.resolve_profile_context(args)
    lab.plot_result(result, args, context, (v, observed, np.zeros_like(v)))
    fit = json.loads((tmp_path / "edge.json").read_text())["terminal_velocity"]
    assert fit["spectra"]["observation"]["amplitude"] == pytest.approx(.4)
    assert fit["spectra"]["lab"]["amplitude"] == pytest.approx(20)
    assert fit["observation_minus_lab_kms"] == pytest.approx(5)


@pytest.mark.parametrize("options", [
    ["--terminal-sigma", "0"], ["--terminal-sigma", "nan"],
    ["--terminal-return-width", "0"], ["--terminal-return-width", "nan"],
    ["--terminal-min-channels", "0"], ["--noise-range", "100", "50"],
    ["--noise-range", "nan", "50"], ["--terminal-source", "observation"],
    ["--terminal-method", "erfc", "--vmax", "-10"],
    ["--terminal-method", "erfc", "--terminal-fit-range", "-10", "80"],
    ["--terminal-method", "erfc", "--terminal-fit-range", "80", "20"],
    ["--terminal-method", "erfc", "--terminal-fit-range", "20", "150"],
])
def test_invalid_terminal_options_fail_before_cube_access(options, monkeypatch):
    monkeypatch.setattr(lab, "LABGaussianExtractor", lambda *a: pytest.fail("No cube access"))
    with pytest.raises(SystemExit) as exc:
        lab.main(["--lon", "50", "--lat", "0", *options, "--noise-estimator", "mean_rms", "--noise-selection", "positive_edge"])
    assert exc.value.code == 2


def test_noise_diagnostics_do_not_invoke_terminal_detection(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("Noise/SNR diagnostics must not detect a terminal edge")
    monkeypatch.setattr(lab, "estimate_terminal_velocity", unexpected)
    velocity, power = terminal_test_spectrum()
    original = power.copy()
    result = lab.observation_noise_metrics(velocity, power, -100, 100, [[-40, -21]], noise_estimator="mean_rms", noise_selection="positive_edge")
    assert result['noise_estimator'] == 'mean_rms'
    assert result['peak_snr'] == 10
    np.testing.assert_array_equal(power, original)
