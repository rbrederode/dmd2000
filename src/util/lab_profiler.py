"""Extract a LAB H I reference profile for an observation and physical scan.

From src: python -m util.lab_profiler --obs OBS_ID --scan SCAN_ID --dir PATH
Survey description: https://lambda.gsfc.nasa.gov/product/foreground/fg_LAB_HI_Survey_info.html
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import sys
import tarfile
import tempfile
from urllib.request import urlopen

import numpy as np
from astropy import units as u
from astropy.coordinates import AltAz, EarthLocation, SkyCoord
from astropy.io import fits
from astropy.time import Time
from astropy.utils import iers

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models.obs import ObsModel

LAB_ARCHIVE_URL = "https://lambda.gsfc.nasa.gov/data/foregrounds/HI/lab_healpix.tar.bz2"
SLICE_NAME = re.compile(r"LAB[_-]cut([+-]?\d+)\.fits$", re.IGNORECASE)
BIN_WIDTH_KMS = 10.0


@dataclass(frozen=True)
class LabProfile:
    longitude_deg: float
    latitude_deg: float
    observed_at: str
    velocity_lsr_kms: np.ndarray
    brightness_k: np.ndarray
    weight: np.ndarray


def observation_pointing(directory, obs_id, scan_id):
    """Read metadata only; prefer actual acquired Alt/Az for fixed pointings."""
    obs = ObsModel.load_from_disk(input_dir=str(Path(directory).expanduser()), filename=f"{obs_id}-obs.json")
    matches = [scan for group in obs.target_scans for scan in group.scans if scan.scan_id == scan_id]
    if len(matches) != 1:
        available = ", ".join(scan.scan_id for group in obs.target_scans for scan in group.scans)
        raise ValueError(f"Expected one scan matching {scan_id!r}; found {len(matches)}. Available scans: {available}")
    scan = matches[0]
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
            altaz = scan.tgt_acq_altaz or target.altaz
            if altaz is None:
                raise ValueError(f"Scan {scan_id} has neither sky coordinates nor Alt/Az")
            pointing = SkyCoord(
                alt=altaz["alt"] * u.deg, az=altaz["az"] * u.deg,
                frame=AltAz(location=location, obstime=Time(midpoint), pressure=0 * u.hPa),
            )
        galactic = pointing.galactic
    return galactic, midpoint


def find_slices(directory):
    """Find velocity slices recursively; reject ambiguous duplicate velocity bins."""
    slices = {}
    directory = Path(directory).expanduser()
    for path in directory.rglob("*.fits"):
        match = SLICE_NAME.fullmatch(path.name)
        if match:
            lower = float(match[1])
            if lower in slices:
                raise ValueError(f"Duplicate LAB velocity slice at {lower:g} km/s")
            slices[lower] = path
    return sorted(slices.items())


@contextmanager
def survey_slices(lab_dir=None):
    """Reuse local slices, or download/extract to a folder removed on exit.

    Only regular velocity-slice files are copied, with flat validated names;
    archive links and unrelated files are never extracted.
    """
    local = Path(lab_dir).expanduser() if lab_dir else Path.home() / "Downloads" / "LAB_Healpix"
    slices = find_slices(local)
    if slices:
        yield slices
        return
    with tempfile.TemporaryDirectory(prefix="dmd2000-lab-") as temporary:
        folder = Path(temporary)
        archive = folder / "lab_healpix.tar.bz2"
        print(f"No local LAB slices found in {local}. Downloading {LAB_ARCHIVE_URL} to {folder}", file=sys.stderr)
        with urlopen(LAB_ARCHIVE_URL, timeout=120) as response, archive.open("wb") as output:
            shutil.copyfileobj(response, output)
        with tarfile.open(archive, "r:bz2") as bundle:
            for member in bundle:
                name = Path(member.name).name
                if member.isfile() and SLICE_NAME.fullmatch(name):
                    destination = folder / name
                    with bundle.extractfile(member) as source, destination.open("xb") as output:
                        shutil.copyfileobj(source, output)
        archive.unlink()
        slices = find_slices(folder)
        if not slices:
            raise ValueError("Downloaded LAB archive contains no velocity slices")
        yield slices


def extract_profile(slices, galactic, observed_at):
    """Read one nearest HEALPix pixel per 10 km/s slice (no beam averaging).

    NASA's TEMPERATURE field is already mean brightness in K; the second field
    counts contributing channels. Neither dividing by bin width nor another
    Earth-motion correction is appropriate for these LSR survey slices.
    """
    try:
        import healpy as hp
    except ImportError as exc:
        raise RuntimeError("LAB extraction requires healpy: python -m pip install healpy") from exc
    longitude, latitude = galactic.l.deg, galactic.b.deg
    velocities, brightness, weights = [], [], []
    for lower, path in slices:
        with fits.open(path, memmap=True) as hdul:
            table = hdul[1]
            header = table.header
            if header.get("INDXSCHM", "IMPLICIT").strip().upper() != "IMPLICIT":
                raise ValueError(f"Unsupported explicit HEALPix indexing in {path}")
            if header.get("COORDSYS", "G").strip().upper() != "G":
                raise ValueError(f"Expected Galactic LAB coordinates in {path}")
            ordering = header["ORDERING"].strip().upper()
            if ordering not in ("RING", "NESTED"):
                raise ValueError(f"Unknown HEALPix ordering {ordering} in {path}")
            pixel = hp.ang2pix(int(header["NSIDE"]), longitude, latitude, lonlat=True, nest=ordering == "NESTED")
            names = [name.upper() for name in table.columns.names]
            temperature_index = names.index("TEMPERATURE")
            weight_index = next((names.index(name) for name in ("SIMULATION", "WEIGHT") if name in names), None)
            if weight_index is None:
                raise ValueError(f"No contributing-channel count field in {path}")
            value = float(table.data.field(temperature_index).reshape(-1)[pixel])
            weight = float(table.data.field(weight_index).reshape(-1)[pixel])
            unit = table.columns[temperature_index].unit
            if unit:
                value = (value * u.Unit(unit)).to_value(u.K)
        if weight <= 0 or not np.isfinite(weight) or not np.isfinite(value) or np.isclose(value, hp.UNSEEN):
            value = np.nan
        velocities.append(lower + BIN_WIDTH_KMS / 2)
        brightness.append(value)
        weights.append(weight)
    if not velocities:
        raise ValueError("No LAB velocity slices supplied")
    return LabProfile(float(longitude), float(latitude), observed_at.isoformat(), np.array(velocities), np.array(brightness), np.array(weights))


def save_profile(profile, prefix, show=True):
    """Save CSV and PNG outside the temporary survey directory."""
    import matplotlib.pyplot as plt

    prefix = Path(prefix).expanduser()
    prefix.parent.mkdir(parents=True, exist_ok=True)
    csv_path, png_path = Path(f"{prefix}.csv"), Path(f"{prefix}.png")
    np.savetxt(csv_path, np.column_stack([profile.velocity_lsr_kms, profile.brightness_k, profile.weight]),
               delimiter=",", header="velocity_lsr_kms,mean_brightness_K,weight", comments="")
    figure, axes = plt.subplots(figsize=(12, 5))
    axes.step(profile.velocity_lsr_kms, profile.brightness_k, where="mid", label="LAB H I (10 km/s bins)")
    axes.axvline(0, color="black", linestyle="--", alpha=0.7, label="Zero LSR velocity")
    axes.set(xlabel="LSR velocity [km/s]", ylabel="Mean H I brightness temperature [K]",
             title=f"{prefix.name}\nl={profile.longitude_deg:.3f}°, b={profile.latitude_deg:.3f}°; {profile.observed_at}")
    axes.grid(alpha=0.3)
    axes.legend()
    figure.tight_layout()
    figure.savefig(png_path, dpi=180)
    if show:
        plt.show()
    plt.close(figure)
    return csv_path, png_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--obs", "--observation-id", required=True, dest="obs")
    parser.add_argument("--scan", "--scan-id", required=True, dest="scan")
    parser.add_argument("--dir", default=".", help="Directory containing observation metadata")
    parser.add_argument("--lab-dir", help="Extracted LAB FITS directory; defaults to ~/Downloads/LAB_Healpix")
    parser.add_argument("--output-dir", help="Output directory; defaults to --dir")
    parser.add_argument("--no-show", action="store_true", help="Save the plot without opening a window")
    args = parser.parse_args(argv)
    try:
        # Check dependencies before initiating a potentially large download.
        import healpy  # noqa: F401
        galactic, observed_at = observation_pointing(args.dir, args.obs, args.scan)
        print(f"Scan midpoint UTC: {observed_at.isoformat()}; l={galactic.l.deg:.4f}°, b={galactic.b.deg:.4f}°")
        with survey_slices(args.lab_dir) as slices:
            print(f"Extracting {len(slices)} LAB velocity bins")
            profile = extract_profile(slices, galactic, observed_at)
        name = re.sub(r"[^A-Za-z0-9_.+-]", "_", args.scan)
        prefix = Path(args.output_dir or args.dir).expanduser() / f"{name}-lab-profile"
        for path in save_profile(profile, prefix, show=not args.no_show):
            print(f"Saved: {path}")
    except ImportError as exc:
        parser.exit(1, f"LAB extraction dependency missing: {exc}. Install with python -m pip install healpy\n")
    except (OSError, ValueError, RuntimeError, KeyError, tarfile.TarError) as exc:
        parser.exit(1, f"LAB extraction failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
