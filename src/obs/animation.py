"""Create an animated HI spectrum from processed DMD2000 observation scans."""

from __future__ import annotations

import argparse
import logging
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import astropy.units as u
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import animation as mpl_animation
import numpy as np
from astropy.coordinates import AltAz, EarthLocation, SkyCoord, get_body
from astropy.time import Time
from astropy.utils import iers

from models.target import PointingType
from obs.opt import process_observation
from util.util import f_e, freq2vel, velocity2LSR


logger = logging.getLogger(__name__)
iers.conf.auto_download = False


@dataclass
class AnimationFrame:
    """Processed spectrum and sky position for one physical SKY scan."""

    scan_id: str
    time: datetime
    ra_deg: float
    dec_deg: float
    galactic_l_deg: float
    galactic_b_deg: float
    velocity_lsr_kms: np.ndarray
    spectrum: np.ndarray


def earth_location_for_observation(obs_model) -> EarthLocation:
    """Use the dish location archived in the observation metadata."""
    return EarthLocation(
        lat=float(obs_model.latitude) * u.deg,
        lon=float(obs_model.longitude) * u.deg,
        height=float(obs_model.height) * u.m,
    )


def scan_midpoint(scan_model) -> datetime:
    """Return the midpoint of a physical scan's recorded sample interval."""
    if scan_model.read_start is None:
        raise ValueError(f"Scan {scan_model.scan_id} has no read_start timestamp")
    if scan_model.read_end is None:
        return scan_model.read_start
    return scan_model.read_start + (scan_model.read_end - scan_model.read_start) / 2


def target_for_scan(obs_model, scan_model):
    """Resolve the target referenced by a scan."""
    target = obs_model.get_target_by_index(scan_model.tgt_idx)
    if target is None:
        raise ValueError(
            f"Observation {obs_model.obs_id} has no target at index {scan_model.tgt_idx}"
        )
    return target


def target_icrs_at_time(target, observing_time: datetime, location: EarthLocation) -> SkyCoord:
    """Resolve supported target pointing modes into an ICRS coordinate."""
    time = Time(observing_time)

    if target.pointing == PointingType.DRIFT_SCAN:
        if target.altaz is None:
            raise ValueError("DRIFT_SCAN target has no fixed Alt/Az coordinate")
        alt = target.altaz.get("alt") if isinstance(target.altaz, dict) else target.altaz.alt
        az = target.altaz.get("az") if isinstance(target.altaz, dict) else target.altaz.az
        altaz = SkyCoord(
            alt=float(alt) * u.deg,
            az=float(az) * u.deg,
            frame=AltAz(obstime=time, location=location),
        )
        return altaz.transform_to("icrs")

    if target.pointing == PointingType.SIDEREAL_TRACK:
        if target.sky_coord is None:
            raise ValueError("SIDEREAL_TRACK target has no sky_coord")
        return target.sky_coord.transform_to("icrs")

    if target.pointing == PointingType.NON_SIDEREAL_TRACK:
        if not target.id:
            raise ValueError("NON_SIDEREAL_TRACK target has no solar-system body id")
        return get_body(target.id, time, location).transform_to("icrs")

    raise ValueError(
        f"Pointing type {target.pointing.name} is not supported by the HI animation"
    )


def scan_velocity_axis_lsr(scan_model, target_icrs: SkyCoord, location: EarthLocation, observing_time: datetime) -> np.ndarray:
    """Build the scan's per-channel LSR velocity axis from its metadata."""
    channels = int(scan_model.spectral_resolution)
    sample_rate = float(scan_model.sample_rate)
    center_frequency = float(scan_model.center_freq)
    if channels <= 0 or sample_rate <= 0 or center_frequency <= 0:
        raise ValueError(
            f"Scan {scan_model.scan_id} has invalid spectral metadata: "
            f"channels={channels}, sample_rate={sample_rate}, center_freq={center_frequency}"
        )

    channel_offsets = np.fft.fftshift(
        np.fft.fftfreq(channels, d=1.0 / sample_rate)
    )
    frequencies = (center_frequency + channel_offsets) * u.Hz
    observer_adjustment = velocity2LSR(
        coord=target_icrs,
        observing_location=location,
        observing_time=Time(observing_time),
    )
    return (
        freq2vel(frequencies, rest=f_e) - observer_adjustment
    ).to_value(u.km / u.s)


def build_animation_frames(obs_model, scans, location: EarthLocation) -> list[AnimationFrame]:
    """Convert processed physical SKY scans into chronological animation frames."""
    frames = []
    sorted_scans = sorted(
        scans,
        key=lambda scan: scan.scan_model.read_start or scan.scan_model.created,
    )

    for scan in sorted_scans:
        scan_model = scan.scan_model
        spectrum = np.asarray(scan.mpr, dtype=np.float64).squeeze()
        if spectrum.ndim != 1:
            raise ValueError(
                f"Scan {scan_model.scan_id} MPR must be one-dimensional, got {spectrum.shape}"
            )
        if spectrum.size != int(scan_model.spectral_resolution):
            raise ValueError(
                f"Scan {scan_model.scan_id} MPR contains {spectrum.size} channels, "
                f"metadata specifies {scan_model.spectral_resolution}"
            )

        observing_time = scan_midpoint(scan_model)
        target = target_for_scan(obs_model, scan_model)
        target_icrs = target_icrs_at_time(target, observing_time, location)
        galactic = target_icrs.transform_to("galactic")
        velocity = scan_velocity_axis_lsr(
            scan_model,
            target_icrs,
            location,
            observing_time,
        )

        velocity_order = np.argsort(velocity)
        frames.append(
            AnimationFrame(
                scan_id=scan_model.scan_id,
                time=observing_time,
                ra_deg=float(target_icrs.ra.deg),
                dec_deg=float(target_icrs.dec.deg),
                galactic_l_deg=float(galactic.l.deg),
                galactic_b_deg=float(galactic.b.deg),
                velocity_lsr_kms=velocity[velocity_order],
                spectrum=spectrum[velocity_order],
            )
        )

    return frames


def _plot_limits(frames: list[AnimationFrame]):
    velocity = np.concatenate([frame.velocity_lsr_kms for frame in frames])
    spectra = np.concatenate([frame.spectrum for frame in frames])
    finite_velocity = velocity[np.isfinite(velocity)]
    finite_spectra = spectra[np.isfinite(spectra)]
    if finite_velocity.size == 0 or finite_spectra.size == 0:
        raise ValueError("Animation frames contain no finite velocity or spectrum values")

    x_limits = (float(np.min(finite_velocity)), float(np.max(finite_velocity)))
    y_low, y_high = np.percentile(finite_spectra, [1.0, 99.0])
    if y_low == y_high:
        padding = max(abs(float(y_low)) * 0.05, 1.0)
    else:
        padding = float(y_high - y_low) * 0.1
    return x_limits, (float(y_low - padding), float(y_high + padding))


def create_animation(
    frames: list[AnimationFrame],
    location: EarthLocation,
    output_path: str | Path,
    interval_ms: int = 100,
) -> Path:
    """Render processed frames as a star-chart and HI-spectrum GIF."""
    if not frames:
        raise ValueError("No processed SKY scans are available for animation")

    try:
        from obs.animation_shin import generate_star_chart
    except ModuleNotFoundError as exc:
        if exc.name == "starplot":
            raise RuntimeError(
                "The starplot package is required to generate the animation. "
                "Install dependencies from src/requirements.txt."
            ) from exc
        raise

    output_path = Path(output_path).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    x_limits, y_limits = _plot_limits(frames)
    longitude_deg = float(location.lon.to_value(u.deg))
    latitude_deg = float(location.lat.to_value(u.deg))

    with tempfile.TemporaryDirectory(prefix="dmd2000-hi-animation-") as temp_dir:
        chart_path = Path(temp_dir) / "star-chart.png"
        first = frames[0]
        first_chart = generate_star_chart(
            first.time,
            longitude_deg,
            latitude_deg,
            first.ra_deg,
            first.dec_deg,
            filename=str(chart_path),
        )

        figure, (sky_axis, spectrum_axis) = plt.subplots(1, 2, figsize=(11, 5))
        chart_artist = sky_axis.imshow(first_chart)
        sky_axis.axis("off")
        title_artist = sky_axis.set_title("")
        spectrum_line, = spectrum_axis.plot([], [], color="tab:blue")
        spectrum_axis.set_xlim(*x_limits)
        spectrum_axis.set_ylim(*y_limits)
        spectrum_axis.set_xlabel(r"$V_{\mathrm{LSR}}$ [km/s]")
        spectrum_axis.set_ylabel("Processed MPR [a.u.]")
        spectrum_axis.grid(alpha=0.2)
        figure.subplots_adjust(wspace=0.3)

        def update(frame_index):
            frame = frames[frame_index]
            chart = generate_star_chart(
                frame.time,
                longitude_deg,
                latitude_deg,
                frame.ra_deg,
                frame.dec_deg,
                filename=str(chart_path),
            )
            chart_artist.set_data(chart)
            spectrum_line.set_data(frame.velocity_lsr_kms, frame.spectrum)
            title_artist.set_text(
                f"{frame.time:%Y-%m-%d %H:%M:%S} UTC\n"
                f"(l, b) = ({frame.galactic_l_deg:.1f}°, {frame.galactic_b_deg:.1f}°)"
            )
            spectrum_axis.set_title(frame.scan_id)
            return chart_artist, spectrum_line, title_artist

        animation = mpl_animation.FuncAnimation(
            figure,
            update,
            frames=len(frames),
            interval=interval_ms,
            blit=False,
        )
        frames_per_second = max(1.0, 1000.0 / interval_ms)
        animation.save(
            output_path,
            writer=mpl_animation.PillowWriter(fps=frames_per_second),
        )
        plt.close(figure)

    return output_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create an animated, pipeline-processed HI observation."
    )
    parser.add_argument(
        "-d",
        "--dir",
        required=True,
        type=Path,
        help="Directory containing <obs_id>-obs.json and associated scan files",
    )
    parser.add_argument("-o", "--obs", required=True, help="Observation ID")
    parser.add_argument(
        "-p",
        "--profile",
        required=True,
        help="Configuration profile providing PipelineConfig.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output GIF path (default: <dir>/<obs_id>-hi-animation.gif)",
    )
    parser.add_argument(
        "--interval-ms",
        type=int,
        default=100,
        help="Animation frame interval in milliseconds (default: 100)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output_path = args.output or args.dir / f"{args.obs}-hi-animation.gif"

    try:
        observation, _, _, processed_scans = process_observation(
            directory=str(args.dir),
            obs_id=args.obs,
            profile=args.profile,
            retain_sky_scans=True,
        )
        location = earth_location_for_observation(observation.obs_model)
        frames = build_animation_frames(
            observation.obs_model,
            processed_scans,
            location,
        )
        result = create_animation(
            frames,
            location,
            output_path,
            interval_ms=args.interval_ms,
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        logger.error("HI animation failed: %s", exc)
        print(f"HI animation failed: {exc}")
        return 1

    print(f"Created {len(frames)}-frame HI animation: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
