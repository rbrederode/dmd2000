from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import astropy.units as u
import numpy as np
from astropy.coordinates import EarthLocation, SkyCoord

from models.target import PointingType
from obs.animation import (
    AnimationFrame,
    _bandpass_regions,
    _plot_limits,
    build_animation_frames,
    create_animation,
    earth_location_for_observation,
    scan_frequency_axis_mhz,
    target_icrs_at_time,
)
from obs import opt
from sdp.channel_mask import ChannelFlag, empty_channel_flags


def test_earth_location_uses_archived_observation_location():
    obs_model = SimpleNamespace(latitude=53.2421, longitude=-2.3067, height=78.0)

    location = earth_location_for_observation(obs_model)

    assert np.isclose(location.lat.to_value(u.deg), obs_model.latitude)
    assert np.isclose(location.lon.to_value(u.deg), obs_model.longitude)
    assert np.isclose(location.height.to_value(u.m), obs_model.height)


def test_drift_scan_frame_uses_scan_midpoint_and_spectral_metadata():
    start = datetime(2026, 7, 27, 9, 0, tzinfo=timezone.utc)
    target = SimpleNamespace(
        pointing=PointingType.DRIFT_SCAN,
        altaz={"alt": 90.0, "az": 0.0},
        sky_coord=None,
        id=None,
    )
    obs_model = SimpleNamespace(
        obs_id="obs001",
        get_target_by_index=lambda tgt_idx: target if tgt_idx == 0 else None,
    )
    scan_model = SimpleNamespace(
        scan_id="obs001-0-0-0",
        tgt_idx=0,
        read_start=start,
        read_end=start + timedelta(seconds=60),
        created=start,
        spectral_resolution=8,
        sample_rate=2_048_000.0,
        center_freq=1_420_000_000.0,
    )
    mpr_flags = empty_channel_flags(8)
    mpr_flags[[0, 1]] |= int(ChannelFlag.BANDPASS_EXCLUDED)
    scan = SimpleNamespace(
        scan_model=scan_model,
        mpr=np.arange(8, dtype=float),
        mpr_flags=mpr_flags,
    )
    location = EarthLocation(
        lat=53.23409 * u.deg,
        lon=-2.305533 * u.deg,
        height=78.0 * u.m,
    )

    frames = build_animation_frames(obs_model, [scan], location)

    assert len(frames) == 1
    assert frames[0].time == start + timedelta(seconds=30)
    assert frames[0].frequency_mhz.shape == (8,)
    assert frames[0].spectrum.shape == (8,)
    np.testing.assert_allclose(
        frames[0].frequency_mhz,
        np.array([1418.976, 1419.232, 1419.488, 1419.744, 1420.0, 1420.256, 1420.512, 1420.768]),
    )
    np.testing.assert_array_equal(frames[0].spectrum, scan.mpr)
    np.testing.assert_array_equal(frames[0].channel_flags, scan.mpr_flags)


def test_animation_masks_flagged_limits_and_shades_bandpass():
    flags = empty_channel_flags(6)
    flags[[0, 1, 5]] |= int(ChannelFlag.BANDPASS_EXCLUDED)
    flags[3] |= int(ChannelFlag.RFI_DETECTED)
    frame = AnimationFrame(
        scan_id="obs001-0-0-0",
        time=datetime(2026, 7, 27, 9, 0, 30, tzinfo=timezone.utc),
        ra_deg=120.0,
        dec_deg=30.0,
        galactic_l_deg=190.0,
        galactic_b_deg=10.0,
        frequency_mhz=np.arange(6, dtype=float) + 1409.0,
        spectrum=np.array([1000.0, 1000.0, 3.0, 4000.0, 5.0, 1000.0]),
        channel_flags=flags,
    )

    _, y_limits = _plot_limits([frame])
    assert y_limits[0] < 3.0
    assert y_limits[1] < 6.0
    assert _bandpass_regions(frame) == [(1408.5, 1410.5), (1413.5, 1414.5)]


def test_frequency_axis_uses_fft_channel_centres():
    scan_model = SimpleNamespace(
        scan_id="obs001-0-0-0",
        spectral_resolution=4,
        sample_rate=2_000_000.0,
        center_freq=1_410_000_000.0,
    )

    np.testing.assert_allclose(
        scan_frequency_axis_mhz(scan_model),
        [1409.0, 1409.5, 1410.0, 1410.5],
    )


def test_sidereal_target_is_supported():
    target = SimpleNamespace(
        pointing=PointingType.SIDEREAL_TRACK,
        sky_coord=SkyCoord(ra=120.0 * u.deg, dec=30.0 * u.deg, frame="icrs"),
        altaz=None,
        id=None,
    )
    location = EarthLocation(lat=53.0 * u.deg, lon=-2.0 * u.deg, height=80 * u.m)

    coordinate = target_icrs_at_time(
        target,
        datetime(2026, 7, 27, tzinfo=timezone.utc),
        location,
    )

    assert np.isclose(coordinate.ra.deg, 120.0)
    assert np.isclose(coordinate.dec.deg, 30.0)


def test_non_sidereal_target_is_supported():
    target = SimpleNamespace(
        pointing=PointingType.NON_SIDEREAL_TRACK,
        sky_coord=None,
        altaz=None,
        id="sun",
    )
    location = EarthLocation(lat=53.0 * u.deg, lon=-2.0 * u.deg, height=80 * u.m)

    coordinate = target_icrs_at_time(
        target,
        datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc),
        location,
    )

    assert np.isfinite(coordinate.ra.deg)
    assert np.isfinite(coordinate.dec.deg)


def test_process_observation_reuses_pipeline_and_retains_physical_sky_scans(monkeypatch):
    calls = []
    retained_scan = object()

    class FakeObservation:
        def integrate_cal_scans(self, **kwargs):
            calls.append("cal")

        def synthesise_integrated_scans(self, **kwargs):
            calls.append("synthesise")

        def integrate_sky_scans(self, processed_scans=None, **kwargs):
            calls.append("sky")
            processed_scans.append(retained_scan)

    fake_observation = FakeObservation()
    fake_factory = object()
    fake_observation_class = SimpleNamespace(
        init_pipeline_factory=lambda input_dir: fake_factory,
        from_disk=lambda **kwargs: fake_observation,
    )
    monkeypatch.setattr(opt, "Observation", fake_observation_class)

    observation, _, _, processed_scans = opt.process_observation(
        directory="/tmp/observation-data",
        obs_id="obs001",
        profile="jodrell",
        retain_sky_scans=True,
    )

    assert observation is fake_observation
    assert processed_scans == [retained_scan]
    assert calls == ["cal", "synthesise", "sky", "synthesise"]


def test_create_animation_writes_gif(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "obs.animation.generate_star_chart",
        lambda *args, **kwargs: np.ones((16, 16, 3), dtype=np.float32),
    )

    channel_flags = empty_channel_flags(8)
    channel_flags[[0, 1]] |= int(ChannelFlag.BANDPASS_EXCLUDED)
    channel_flags[5] |= int(ChannelFlag.RFI_DETECTED)
    frame = AnimationFrame(
        scan_id="obs001-0-0-0",
        time=datetime(2026, 7, 27, 9, 0, 30, tzinfo=timezone.utc),
        ra_deg=120.0,
        dec_deg=30.0,
        galactic_l_deg=190.0,
        galactic_b_deg=10.0,
        frequency_mhz=np.linspace(1419.0, 1421.0, 8),
        spectrum=np.linspace(0.9, 1.1, 8),
        channel_flags=channel_flags,
    )
    location = EarthLocation(
        lat=53.23409 * u.deg,
        lon=-2.305533 * u.deg,
        height=78.0 * u.m,
    )
    output_path = tmp_path / "animation.gif"

    result = create_animation([frame], location, output_path, interval_ms=100)

    assert result == output_path
    assert output_path.is_file()
    assert output_path.stat().st_size > 0
