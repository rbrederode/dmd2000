import csv
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import matplotlib
import numpy as np
from astropy import units as u
from astropy.coordinates import EarthLocation, SkyCoord
from astropy.constants import c

matplotlib.use("Agg")
from matplotlib import pyplot as plt

from models.scan import ScanState, ScanType
from obs.lsr import HI_REST_FREQUENCY, lsr_velocity, scan_observing_context
from obs.obs_display import ObsDisplay
from sdp.channel_mask import ChannelFlag, empty_channel_flags


def make_observation():
    start = datetime(2026, 10, 24, 11, 25, tzinfo=timezone.utc)
    target = SimpleNamespace(sky_coord=None, altaz={"alt": 24, "az": 48})
    physical = SimpleNamespace(
        tgt_idx=1, freq_scan=0, dig_id="dig003", scan_type=ScanType.SKY,
        status=ScanState.COMPLETE, scan_id="physical", read_start=start,
        read_end=start + timedelta(seconds=20),
    )
    obs = SimpleNamespace(
        latitude=53.234, longitude=-2.305, height=77,
        target_scans=[SimpleNamespace(scans=[physical])],
        get_target_by_index=lambda idx: target,
    )
    model = SimpleNamespace(
        tgt_idx=1, freq_scan=0, dig_id="dig003", scan_id="aggregate",
        center_freq=HI_REST_FREQUENCY.to_value(u.Hz), sample_rate=1e6,
        spectral_resolution=8,
    )
    flags = empty_channel_flags(8)
    flags[0] |= int(ChannelFlag.BANDPASS_EXCLUDED)
    flags[4] |= int(ChannelFlag.RFI_DETECTED)
    scan = SimpleNamespace(scan_model=model, mpr=np.arange(8, dtype=float), mpr_flags=flags)
    return obs, scan


def test_lsr_velocity_preserves_radio_doppler_scale_and_redshift_sign():
    location = EarthLocation.from_geodetic(-2.305 * u.deg, 53.234 * u.deg, 77 * u.m)
    direction = SkyCoord(ra=120 * u.deg, dec=30 * u.deg)
    rest = HI_REST_FREQUENCY.to_value(u.Hz)
    frequencies = np.array([rest, rest - 100000, rest + 100000])
    velocity = lsr_velocity(frequencies, direction, location, "2026-10-24T11:25:00")
    # The frame change scales frequencies equally: radio velocity is linear in f.
    expected = (c.to_value(u.km / u.s) - velocity[0]) * (1 - frequencies / rest)
    np.testing.assert_allclose(velocity - velocity[0], expected, atol=1e-7)
    assert -60 < velocity[0] < 60
    assert velocity[1] > velocity[0] > velocity[2]


def test_altaz_context_uses_physical_midpoint_and_respects_blacklist():
    import pytest
    obs, scan = make_observation()
    direction, location, observed = scan_observing_context(obs, scan)
    assert observed == datetime(2026, 10, 24, 11, 25, 10, tzinfo=timezone.utc)
    assert direction.frame.name == "icrs"
    assert np.isfinite(direction.ra.deg)
    with pytest.raises(ValueError, match="timestamps"):
        scan_observing_context(obs, scan, ["physical"])


def test_lsr_plot_multiple_frequencies_retains_masks_and_power(tmp_path):
    obs, scan = make_observation()
    second = SimpleNamespace(
        scan_model=SimpleNamespace(**{**vars(scan.scan_model), "freq_scan": 1, "center_freq": 1420905751.768}),
        mpr=scan.mpr + 10, mpr_flags=scan.mpr_flags.copy(),
    )
    original = obs.target_scans[0].scans[0]
    obs.target_scans[0].scans.append(SimpleNamespace(**{**vars(original), "freq_scan": 1, "scan_id": "physical2"}))
    display = ObsDisplay("lsr-test")
    display.scans = [scan, second]
    path = tmp_path / "lsrk.png"
    figure = display.display_lsr(obs, str(path), observation_baseline="none", show_metrics=False)
    try:
        axes = figure.axes[0]
        assert not any(text.get_gid() == 'spectrum-noise-metrics' for text in axes.texts)
        assert len([line for line in axes.lines if line.get_label().startswith("Tgt ")]) == 2
        assert "RFI Flagged" in axes.get_legend_handles_labels()[1]
        assert "Bandpass Excluded" in axes.get_legend_handles_labels()[1]
        assert "LSRK" in axes.get_xlabel()
        assert "radio convention" in axes.get_xlabel()
        for line, source in zip([line for line in axes.lines if line.get_label().startswith("Tgt ")], [scan, second]):
            assert np.all(np.diff(line.get_xdata()) > 0)
            valid = ~np.ma.getmaskarray(line.get_ydata())
            np.testing.assert_array_equal(line.get_ydata().data[valid], source.mpr[::-1][valid])
            np.testing.assert_array_equal(np.ma.getmaskarray(line.get_ydata()), [False, False, False, True, False, False, False, True])
        assert path.read_bytes().startswith(b"\x89PNG")
    finally:
        plt.close(figure)


def test_lsr_csv_matches_plotted_samples_for_multiple_targets(tmp_path):
    obs, scan = make_observation()
    target = obs.get_target_by_index(1)
    target.id = "Stow Position: AltAz (18,48)"
    second_target = SimpleNamespace(**{**vars(target), "id": "target-two"})
    obs.get_target_by_index = lambda idx: target if idx == 1 else second_target
    second = SimpleNamespace(
        scan_model=SimpleNamespace(**{**vars(scan.scan_model), "tgt_idx": 2, "freq_scan": 1}),
        mpr=scan.mpr + 10, mpr_flags=scan.mpr_flags.copy(),
    )
    original = obs.target_scans[0].scans[0]
    obs.target_scans[0].scans.append(SimpleNamespace(**{**vars(original), "tgt_idx": 2, "freq_scan": 1}))
    display = ObsDisplay("csv-test")
    display.scans = [scan, second]
    csv_path = tmp_path / "csv-test_vlsr.csv"

    figure = display.display_lsr(obs, str(tmp_path / "lsrk.png"), csv_path=str(csv_path), observation_baseline="none")
    try:
        with csv_path.open(newline="", encoding="utf-8") as csv_file:
            reader = csv.DictReader(csv_file)
            assert reader.fieldnames == ["target_id", "freq_scan", "velocity_km_s", "calibrated_power", "baseline", "baseline_subtracted_power"]
            rows = list(reader)
        assert len(rows) == 16
        for index, target_id in enumerate([1, 2]):
            target_rows = rows[index * 8:(index + 1) * 8]
            line = [line for line in figure.axes[0].lines if line.get_label().startswith("Tgt ")][index]
            assert all(row["target_id"] == str(target_id) for row in target_rows)
            assert all(row["freq_scan"] == str(index) for row in target_rows)
            np.testing.assert_array_equal(
                [float(row["velocity_km_s"]) for row in target_rows], line.get_xdata(),
            )
            for row, power in zip(target_rows, line.get_ydata()):
                if np.ma.is_masked(power):
                    assert row["calibrated_power"] == ""
                else:
                    assert float(row["calibrated_power"]) == float(power)
    finally:
        plt.close(figure)


def test_lsr_csv_is_not_created_when_no_scans_can_be_plotted(tmp_path):
    obs, scan = make_observation()
    display = ObsDisplay("excluded-test")
    display.scans = [scan]
    csv_path = tmp_path / "excluded-test_vlsr.csv"

    assert display.display_lsr(
        obs, str(tmp_path / "lsrk.png"), blacklist=["physical"], csv_path=str(csv_path),
    ) is None
    assert not csv_path.exists()


def test_lsr_baselines_are_independent_and_csv_keeps_raw_powers(tmp_path, monkeypatch):
    from obs import lsr
    obs, source = make_observation()
    velocity = np.linspace(-100., 150., 251)
    monkeypatch.setattr(lsr, "lsr_velocity", lambda *a: velocity)
    monkeypatch.setattr(lsr, "scan_observing_context", lambda *a: (None, None, None))
    scans = []
    for target, freq, offset, slope, amplitude in [(1, 0, 10, .01, 2), (1, 1, 20, -.01, 4), (2, 0, 30, .02, 3)]:
        model = SimpleNamespace(**{**vars(source.scan_model), "tgt_idx": target, "freq_scan": freq,
                                   "spectral_resolution": len(velocity)})
        power = offset + slope * velocity + amplitude * np.exp(-((velocity - 10) / 10)**2)
        flags = empty_channel_flags(len(velocity))
        power[20] = 10000  # Excluded RFI must never affect the fit.
        flags[20] |= int(ChannelFlag.RFI_DETECTED)
        power[21] = np.nan
        scans.append(SimpleNamespace(scan_model=model, mpr=power, mpr_flags=flags))
    originals = [s.mpr.copy() for s in scans]
    display = ObsDisplay("baseline-test")
    display.scans = scans
    path = tmp_path / "baseline.csv"
    figure = display.display_lsr(obs, str(tmp_path / "baseline.png"), csv_path=str(path))
    try:
        with path.open() as stream:
            rows = list(csv.DictReader(stream))
        for i, scan in enumerate(scans):
            powers = np.ma.masked_invalid(scan.mpr)
            powers.mask[20] = True
            offset, slope = [(10, .01), (20, -.01), (30, .02)][i]
            baseline = np.ma.array(offset + slope * velocity, mask=powers.mask)
            expected = powers - baseline
            line = [line for line in figure.axes[0].lines if line.get_label().startswith("Tgt ")][i]
            np.testing.assert_allclose(line.get_ydata().compressed(), expected.compressed(), atol=1e-12)
            np.testing.assert_array_equal(scan.mpr, originals[i])
            target_rows = rows[i * 251:(i + 1) * 251]
            for j, row in enumerate(target_rows):
                assert row["target_id"] == str(scan.scan_model.tgt_idx)
                assert row["freq_scan"] == str(scan.scan_model.freq_scan)
                if j in [20, 21]:
                    assert row["calibrated_power"] == row["baseline"] == row["baseline_subtracted_power"] == ""
                else:
                    assert float(row["calibrated_power"]) == scan.mpr[j]
                    np.testing.assert_allclose(float(row["baseline"]), baseline[j], atol=1e-12)
                    np.testing.assert_allclose(float(row["baseline_subtracted_power"]), expected[j], atol=1e-12)
        assert "Baseline-subtracted" in figure.axes[0].get_ylabel()
        axis = figure.axes[0]
        figure.canvas.draw()
        assert axis.get_legend().get_window_extent().x0 > axis.get_window_extent().x1
        assert axis.get_position().height > .75

    finally:
        plt.close(figure)


def test_lsr_insufficient_baseline_does_not_silently_plot_raw(tmp_path, caplog):
    obs, scan = make_observation()
    display = ObsDisplay("too-short")
    display.scans = [scan]
    path = tmp_path / "short.png"
    assert display.display_lsr(obs, str(path)) is None
    assert not path.exists()
    assert "target 1, frequency 0" in caplog.text


def test_lsr_baseline_windows_exclude_emission(tmp_path, monkeypatch):
    from obs import lsr
    obs, scan = make_observation()
    velocity = np.arange(-100., 151.)
    monkeypatch.setattr(lsr, "lsr_velocity", lambda *a: velocity)
    monkeypatch.setattr(lsr, "scan_observing_context", lambda *a: (None, None, None))
    scan.scan_model.spectral_resolution = len(velocity)
    baseline = 5 + .01 * velocity
    scan.mpr = baseline.copy()
    scan.mpr[(velocity >= -20) & (velocity <= 60)] += 3
    scan.mpr_flags = empty_channel_flags(len(velocity))
    display = ObsDisplay("windows")
    display.scans = [scan]
    fig = display.display_lsr(obs, str(tmp_path / "windows.png"), baseline_windows=[[-100, -30], [80, 150]])
    try:
        actual = fig.axes[0].lines[0].get_ydata()
        np.testing.assert_allclose(actual, scan.mpr - baseline, atol=1e-12)
    finally:
        plt.close(fig)


def test_lsr_auto_bounds_and_explicit_window_override(tmp_path, monkeypatch):
    from obs import lsr
    obs, scan = make_observation()
    velocity = np.arange(-250., 251.)
    monkeypatch.setattr(lsr, "lsr_velocity", lambda *a: velocity)
    monkeypatch.setattr(lsr, "scan_observing_context", lambda *a: (None, None, None))
    scan.scan_model.spectral_resolution = len(velocity)
    baseline = 5 + .01 * velocity
    scan.mpr = baseline + 20 * (np.abs(velocity) < 190)
    scan.mpr_flags = empty_channel_flags(len(velocity))
    display = ObsDisplay("bounds")
    display.scans = [scan]
    for kwargs in [dict(vmin=-250, vmax=250),
                   dict(vmin=-100, vmax=100, baseline_windows=[[-250, -200], [200, 250]])]:
        figure = display.display_lsr(obs, str(tmp_path / "bounds.png"), **kwargs)
        try:
            np.testing.assert_allclose(figure.axes[0].lines[0].get_ydata(), scan.mpr - baseline, atol=1e-12)
            assert figure.axes[0].get_xlim() == (kwargs["vmin"], kwargs["vmax"])
            assert len(display.lsr_spectra[0]["velocity"]) == len(velocity)
            if "baseline_windows" not in kwargs:
                assert display.lsr_spectra[0]["baseline_windows"] == [[-250., -201.], [201., 250.]]
        finally:
            plt.close(figure)


def test_lsr_legacy_baseline_opt_out(tmp_path, monkeypatch):
    from obs import lsr
    from util.convert import subtract_spectrum_baseline
    obs, scan = make_observation()
    velocity = np.arange(-150., 151.)
    monkeypatch.setattr(lsr, "lsr_velocity", lambda *a: velocity)
    monkeypatch.setattr(lsr, "scan_observing_context", lambda *a: (None, None, None))
    scan.scan_model.spectral_resolution = len(velocity)
    scan.mpr = 5 + .01 * velocity + np.sin(velocity / 10.)
    scan.mpr_flags = empty_channel_flags(len(velocity))
    display = ObsDisplay("legacy")
    display.scans = [scan]
    figure = display.display_lsr(obs, str(tmp_path / "legacy.png"), baseline_auto=False,
                                 baseline_vmin=-150, baseline_vmax=150)
    try:
        expected, _ = subtract_spectrum_baseline(velocity, scan.mpr)
        np.testing.assert_allclose(figure.axes[0].lines[0].get_ydata(), expected)
        assert display.lsr_spectra[0]["baseline_windows"] is None
    finally:
        plt.close(figure)
