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
    figure = display.display_lsr(obs, str(path))
    try:
        axes = figure.axes[0]
        assert len(axes.lines) == 3
        assert "LSRK" in axes.get_xlabel()
        assert "radio convention" in axes.get_xlabel()
        for line, source in zip(axes.lines[:2], [scan, second]):
            assert np.all(np.diff(line.get_xdata()) > 0)
            np.testing.assert_array_equal(line.get_ydata().data, source.mpr[::-1])
            np.testing.assert_array_equal(np.ma.getmaskarray(line.get_ydata()), [False, False, False, True, False, False, False, True])
        assert path.read_bytes().startswith(b"\x89PNG")
    finally:
        plt.close(figure)
