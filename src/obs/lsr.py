"""LSRK spectral coordinates for aggregated H I observations."""

import numpy as np
from astropy import units as u
from astropy.coordinates import AltAz, EarthLocation, SkyCoord, SpectralCoord
from astropy.time import Time
from astropy.utils import iers

HI_REST_FREQUENCY = 1420.405751768 * u.MHz


def lsr_velocity(frequencies_hz, direction, location, observed_at):
    """Convert observed frequencies to LSRK km/s using the radio convention.

    Use bundled Earth orientation data so offline observation replay does not
    require a network download. Astropy still warns when its accuracy is limited.
    """
    with iers.conf.set_temp("auto_download", False), iers.conf.set_temp("auto_max_age", None):
        time = Time(observed_at)
        coordinates = SpectralCoord(
            np.asarray(frequencies_hz) * u.Hz,
            observer=location.get_itrs(obstime=time),
            target=direction,
            doppler_rest=HI_REST_FREQUENCY,
            doppler_convention="radio",
        )
        return coordinates.with_observer_stationary_relative_to("lsrk").to_value(u.km / u.s)


def scan_observing_context(obs, scan, blacklist=()):
    """Recover site, representative time, and direction from original SKY scans.

    Synthetic aggregate timestamps describe reprocessing, so use the midpoint
    of the accepted physical scans' recording span instead. A single correction
    is representative of that aggregate; this does not regrid individual scans.
    """
    model = scan.scan_model
    excluded = set(blacklist)
    physical = [
        item for group in obs.target_scans for item in group.scans
        if item.tgt_idx == model.tgt_idx
        and item.freq_scan == model.freq_scan
        and item.dig_id == model.dig_id
        and item.scan_type.name == "SKY"
        and item.status.name == "COMPLETE"
        and item.scan_id not in excluded
        and item.read_start is not None
    ]
    if not physical:
        raise ValueError("No accepted SKY recording timestamps available")
    start = min(item.read_start for item in physical)
    end = max(item.read_end or item.read_start for item in physical)
    observed_at = start + (end - start) / 2
    location = EarthLocation.from_geodetic(obs.longitude * u.deg, obs.latitude * u.deg, obs.height * u.m)
    target = obs.get_target_by_index(model.tgt_idx)
    if target is None:
        raise ValueError("No target direction available")
    if target.sky_coord is not None:
        direction = target.sky_coord.icrs
    elif target.altaz is not None:
        with iers.conf.set_temp("auto_download", False), iers.conf.set_temp("auto_max_age", None):
            direction = SkyCoord(
                alt=target.altaz["alt"] * u.deg,
                az=target.altaz["az"] * u.deg,
                frame=AltAz(obstime=Time(observed_at), location=location, pressure=0 * u.hPa),
            ).icrs
    else:
        raise ValueError("Target has neither sky coordinates nor Alt/Az")
    return direction, location, observed_at
