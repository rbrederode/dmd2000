"""Create an animated spectrum from processed DMD2000 observation scans."""

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
from sdp.channel_mask import (
    DEFAULT_EXCLUDED_FLAGS,
    ChannelFlag,
    channels_with_flag,
    contiguous_regions,
    empty_channel_flags,
    masked_values,
    valid_channels,
)


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
    frequency_mhz: np.ndarray
    spectrum: np.ndarray
    channel_flags: np.ndarray | None = None


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
        f"Pointing type {target.pointing.name} is not supported by the animation"
    )


def scan_frequency_axis_mhz(scan_model) -> np.ndarray:
    """Build the scan's FFT-shifted channel-centre frequency axis in MHz."""
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
    return (center_frequency + channel_offsets) / 1e6


def generate_star_chart(dt, lon, lat, ra, dec, filename) -> np.ndarray:
    """Generate a zenith chart showing both the Sun and telescope pointing."""
    try:
        from starplot import ZenithPlot, Observer, _
        from starplot.styles import PlotStyle, extensions
    except ModuleNotFoundError as exc:
        if exc.name == "starplot":
            raise RuntimeError(
                "The starplot package is required to generate the animation. "
                "Install dependencies from src/requirements.txt."
            ) from exc
        raise

    observer = Observer(dt=dt, lon=lon, lat=lat)
    plot = ZenithPlot(
        observer=observer,
        style=PlotStyle().extend(
            extensions.BLUE_GOLD,
            extensions.GRADIENT_PRE_DAWN,
            {"milky_way": {"alpha": 0.36, "color": "#FFFFFF"}},
        ),
        resolution=1800,
        autoscale=True,
    )

    plot.sun(label="Sun", legend_label="Sun")
    plot.marker(
        ra=ra,
        dec=dec,
        style={
            "marker": {
                "size": 100,
                "symbol": "circle_cross",
                "fill": "none",
                "color": "yellow",
                "edge_width": 5,
                "alpha": 1,
            },
        },
    )
    plot.horizon()
    plot.constellations()
    plot.stars(where=[_.magnitude < 3], where_labels=[False])
    plot.milky_way()
    plot.export(filename, transparent=True, padding=0.1)
    plot.close_fig()
    return plt.imread(filename)


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
        frequency_mhz = scan_frequency_axis_mhz(scan_model)
        channel_flags = getattr(scan, "mpr_flags", None)
        if (
            not isinstance(channel_flags, np.ndarray)
            or channel_flags.shape != spectrum.shape
            or not np.issubdtype(channel_flags.dtype, np.integer)
        ):
            channel_flags = empty_channel_flags(spectrum.shape)

        frames.append(
            AnimationFrame(
                scan_id=scan_model.scan_id,
                time=observing_time,
                ra_deg=float(target_icrs.ra.deg),
                dec_deg=float(target_icrs.dec.deg),
                galactic_l_deg=float(galactic.l.deg),
                galactic_b_deg=float(galactic.b.deg),
                frequency_mhz=frequency_mhz,
                spectrum=spectrum,
                channel_flags=channel_flags.copy(),
            )
        )

    return frames


def _frame_flags(frame: AnimationFrame) -> np.ndarray:
    """Return channel flags matching a frame, including legacy unflagged frames."""
    flags = frame.channel_flags
    if (
        not isinstance(flags, np.ndarray)
        or flags.shape != frame.spectrum.shape
        or not np.issubdtype(flags.dtype, np.integer)
    ):
        return empty_channel_flags(frame.spectrum.shape)
    return flags


def _bandpass_regions(frame: AnimationFrame) -> list[tuple[float, float]]:
    """Return frequency-edge spans for contiguous bandpass-excluded channels."""
    flags = _frame_flags(frame)
    excluded = channels_with_flag(flags, ChannelFlag.BANDPASS_EXCLUDED)
    if frame.frequency_mhz.size == 0:
        return []

    if frame.frequency_mhz.size == 1:
        half_channel = 0.0
    else:
        half_channel = float(np.median(np.diff(frame.frequency_mhz))) / 2.0
    return [
        (
            float(frame.frequency_mhz[start] - half_channel),
            float(frame.frequency_mhz[end - 1] + half_channel),
        )
        for start, end in contiguous_regions(excluded)
    ]


def _plot_limits(frames: list[AnimationFrame]):
    frequency = np.concatenate([frame.frequency_mhz for frame in frames])
    usable_spectra = [
        frame.spectrum[valid_channels(frame.spectrum, _frame_flags(frame))]
        for frame in frames
    ]
    spectra = np.concatenate(usable_spectra)
    finite_frequency = frequency[np.isfinite(frequency)]
    finite_spectra = spectra[np.isfinite(spectra)]
    if finite_frequency.size == 0 or finite_spectra.size == 0:
        raise ValueError("Animation frames contain no finite frequency or spectrum values")

    x_limits = (float(np.min(finite_frequency)), float(np.max(finite_frequency)))
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
    """Render processed frames as a star-chart and spectrum GIF."""
    if not frames:
        raise ValueError("No processed SKY scans are available for animation")

    output_path = Path(output_path).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    x_limits, y_limits = _plot_limits(frames)
    longitude_deg = float(location.lon.to_value(u.deg))
    latitude_deg = float(location.lat.to_value(u.deg))

    with tempfile.TemporaryDirectory(prefix="dmd2000-animation-") as temp_dir:
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
        spectrum_line, = spectrum_axis.plot([], [], color="tab:blue", label="Processed MPR")
        bandpass_spans = []
        spectrum_axis.set_xlim(*x_limits)
        spectrum_axis.set_ylim(*y_limits)
        spectrum_axis.set_xlabel("Frequency [MHz]")
        spectrum_axis.set_ylabel("Processed MPR [a.u.]")
        spectrum_axis.grid(alpha=0.2)
        figure.subplots_adjust(wspace=0.3, right=0.82)

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
            flags = _frame_flags(frame)
            display_exclusions = DEFAULT_EXCLUDED_FLAGS & ~ChannelFlag.BANDPASS_EXCLUDED
            spectrum_line.set_data(
                frame.frequency_mhz,
                masked_values(
                    frame.spectrum,
                    flags,
                    excluded_flags=display_exclusions,
                ),
            )

            for span in bandpass_spans:
                span.remove()
            bandpass_spans.clear()
            for region_index, (start_mhz, end_mhz) in enumerate(_bandpass_regions(frame)):
                bandpass_spans.append(
                    spectrum_axis.axvspan(
                        start_mhz,
                        end_mhz,
                        color="gray",
                        alpha=0.14,
                        label="Bandpass Excluded" if region_index == 0 else "_nolegend_",
                    )
                )

            title_artist.set_text(
                f"{frame.time:%Y-%m-%d %H:%M:%S} UTC\n"
                f"(l, b) = ({frame.galactic_l_deg:.1f}°, {frame.galactic_b_deg:.1f}°)"
            )
            spectrum_axis.set_title(frame.scan_id)
            legend = spectrum_axis.get_legend()
            if legend is not None:
                legend.remove()
            spectrum_axis.legend(
                loc="upper left",
                bbox_to_anchor=(1.02, 1.0),
                borderaxespad=0,
            )
            return chart_artist, spectrum_line, title_artist, *bandpass_spans

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
        description="Create an animated, pipeline-processed spectrum."
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
        help="Output GIF path (default: <dir>/<obs_id>-animation.gif)",
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
    output_path = args.output or args.dir / f"{args.obs}-animation.gif"

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

    print(f"Created {len(frames)}-frame spectrum animation: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
