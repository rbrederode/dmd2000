"""Shared observation metadata lookup for LAB profiles and Milky Way plots.

Only the observation JSON is read; IQ samples and spectral products are not
loaded. Astropy converts the selected scan's sky direction to Galactic l/b.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import re

import numpy as np
from pathlib import Path

from astropy import units as u
from astropy.coordinates import AltAz, EarthLocation, SkyCoord
from astropy.time import Time
from astropy.utils import iers

from models.obs import ObsModel
from util.convert import datetime_to_utc, parse_utc_datetime


def get_obs_pointing(directory: str | Path, obs_id: str, scan_id: str) -> tuple[SkyCoord, datetime]:
    """Return the Galactic direction and representative time of a physical scan.

    Parameters:
        directory: Folder containing ``<obs_id>-obs.json``. Accepts a string or
            Path; a leading ``~`` is expanded to the user's home directory.
        obs_id: Observation identifier, without the ``-obs.json`` suffix.
        scan_id: Full physical scan identifier stored in the observation's
            target scan sets, normally ``<obs_id>-<tgt_idx>-<freq_scan>-<scan_iter>``.
            The identifier must match exactly one scan.

    Returns:
        A ``(galactic, midpoint)`` tuple:
        * ``galactic`` is an Astropy SkyCoord in the Galactic frame. Access its
          longitude/latitude in degrees as ``galactic.l.deg`` / ``galactic.b.deg``.
        * ``midpoint`` is a datetime halfway between the scan's recording start
          and end. Acquisition time substitutes for a missing recording start;
          a missing end uses that start, making the midpoint equal to the start.
          The datetime retains the metadata's timezone (normally UTC).

    Direction selection:
        Stored target sky coordinates (RA/Dec or another supported celestial
        frame) take precedence. Otherwise, use the scan's actual acquisition
        Alt/Az, falling back to the requested target Alt/Az. Transform this fixed
        pointing at the midpoint using the observation's geodetic longitude,
        latitude and height. Atmospheric refraction is disabled (pressure=0).

    Raises:
        OSError: The observation metadata file cannot be read.
        ValueError: The scan is absent/ambiguous, its time range is invalid, or
            required timing/target/pointing information is missing. Metadata
            deserialisation and coordinate errors also propagate to the caller.

    Earth orientation calculations use local IERS data without downloading or
    rejecting older predictive entries. This can limit coordinate accuracy;
    Astropy's warnings remain enabled. Configuration changes are scoped to this
    call and restored afterwards.
    """
    obs = ObsModel.load_from_disk(input_dir=str(Path(directory).expanduser()), filename=f"{obs_id}-obs.json")
    matches = [scan for group in obs.target_scans for scan in group.scans if scan.scan_id == scan_id]
    if len(matches) != 1:
        available = ", ".join(scan.scan_id for group in obs.target_scans for scan in group.scans)
        raise ValueError(f"Expected one scan matching {scan_id!r}; found {len(matches)}. Available scans: {available}")
    scan = matches[0]
    # Use recorded observing times, never the metadata creation/reprocessing time.
    start = scan.read_start or scan.tgt_acq_dt
    if start is None:
        raise ValueError(f"Scan {scan_id} has no recorded observation time")
    end = scan.read_end or start
    if end < start:
        raise ValueError(f"Scan {scan_id} ends before it starts")
    midpoint = start + (end - start) / 2
    target = obs.get_target_by_index(scan.tgt_idx)
    if target is None:
        raise ValueError(f"Scan {scan_id} has no target metadata")
    location = EarthLocation.from_geodetic(obs.longitude * u.deg, obs.latitude * u.deg, obs.height * u.m)
    with iers.conf.set_temp("auto_download", False), iers.conf.set_temp("auto_max_age", None):
        if target.sky_coord is not None:
            pointing = target.sky_coord
        else:
            # Actual acquisition coordinates describe where the dish pointed;
            # requested coordinates provide a fallback for older observations.
            altaz = scan.tgt_acq_altaz or target.altaz
            if altaz is None:
                raise ValueError(f"Scan {scan_id} has neither sky coordinates nor Alt/Az")
            pointing = SkyCoord(
                alt=altaz["alt"] * u.deg, az=altaz["az"] * u.deg,
                frame=AltAz(location=location, obstime=Time(midpoint), pressure=0 * u.hPa),
            )
        galactic = pointing.galactic
    return galactic, midpoint



# Geodetic latitude (deg), east-positive longitude (deg), height (m), as in alston.
JODRELL_BANK_LOCATION = (53.234123, -2.305485, 77.0)


def add_pointing_arguments(parser):
    """Add the common metadata/direct-coordinate CLI options to a parser.

    Use ``resolve_pointing_context`` after parsing to validate combinations and
    apply Jodrell Bank/current UTC defaults only in direct-coordinate mode.
    """
    parser.add_argument("--obs", "--observation-id", dest="obs")
    parser.add_argument("--scan", "--scan-id", dest="scan")
    parser.add_argument("--galactic", nargs=2, type=float, metavar=("L", "B"), help="Galactic longitude and latitude in degrees eg. 82.2361 -5.6143")
    parser.add_argument("--location", nargs=3, type=float, metavar=("LAT", "LON", "HEIGHT"), help="Geodetic degrees and metres; default Jodrell Bank (53.234123, -2.305485, 77)")
    parser.add_argument("--dt", type=parse_utc_datetime, help="ISO datetime; default current UTC eg. 2026-10-24T11:25:50Z")
    parser.add_argument("--dir", default=".", help="Directory containing observation metadata")
    parser.add_argument("--output-dir", help="Output directory; defaults to --dir")
    parser.add_argument("--no-show", action="store_true", help="Save without opening a window")


@dataclass(frozen=True)
class PointingContext:
    """Resolved input coordinates and provenance, independent of survey access."""

    galactic: SkyCoord
    observed_at: datetime
    location: tuple[float, float, float]
    input_mode: str
    output_name: str
    obs_id: str | None = None
    scan_id: str | None = None

    def metadata(self):
        latitude, longitude, height = self.location
        return {
            "input_mode": self.input_mode,
            "observation_id": self.obs_id,
            "scan_id": self.scan_id,
            "galactic_longitude_deg": float(self.galactic.l.deg),
            "galactic_latitude_deg": float(self.galactic.b.deg),
            "observed_at_utc": self.observed_at.isoformat(),
            "location": {"latitude_deg": latitude, "longitude_deg": longitude, "height_m": height},
        }

def resolve_pointing_context(args, *, now=None):
    """Validate the input mode and return direction, UTC time, site and provenance.

    Direct Galactic coordinates require no metadata. Site/time provide context
    and do not change a direction already expressed in Galactic coordinates.
    ``now`` permits deterministic callers/tests; otherwise current UTC is read
    once when needed. Metadata mode keeps its existing scan midpoint calculation.
    """
    has_metadata = args.obs is not None or args.scan is not None
    if args.galactic is not None:
        if has_metadata:
            raise ValueError("Use either --galactic or --obs with --scan, not both")
        longitude, latitude = args.galactic
        if not np.all(np.isfinite([longitude, latitude])) or not -90 <= latitude <= 90:
            raise ValueError("Galactic coordinates must be finite, with latitude between -90 and 90 degrees")
        site = tuple(args.location) if args.location is not None else JODRELL_BANK_LOCATION
        if not np.all(np.isfinite(site)) or not -90 <= site[0] <= 90 or not -180 <= site[1] <= 180:
            raise ValueError("Location must be finite: latitude -90..90, longitude -180..180, height in metres")
        galactic = SkyCoord(l=longitude * u.deg, b=latitude * u.deg, frame="galactic")
        observed_at = args.dt if args.dt is not None else (now if now is not None else datetime.now(timezone.utc))
        observed_at = datetime_to_utc(observed_at)
        name = f"gal-l{galactic.l.deg:.4f}-b{galactic.b.deg:+.4f}-{observed_at.strftime('%Y%m%dT%H%M%S%fZ')}"
        return PointingContext(galactic, observed_at, site, "galactic", name)
    if args.obs is None or args.scan is None:
        raise ValueError("Provide --galactic L B, or both --obs and --scan")
    if args.location is not None or args.dt is not None:
        raise ValueError("--location and --dt apply only with --galactic; metadata mode uses the recorded site/time")
    galactic, observed_at = get_obs_pointing(args.dir, args.obs, args.scan)
    obs = ObsModel.load_from_disk(input_dir=str(Path(args.dir).expanduser()), filename=f"{args.obs}-obs.json")
    name = re.sub(r"[^A-Za-z0-9_.+-]", "_", args.scan)
    return PointingContext(galactic, datetime_to_utc(observed_at), (obs.latitude, obs.longitude, obs.height),
                          "metadata", name, args.obs, args.scan)

