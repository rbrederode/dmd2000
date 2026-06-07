import argparse
import json
import logging
import os
from pathlib import Path

_mpl_cache = Path(os.environ.get("MPLCONFIGDIR", "/private/tmp/dmd2000-matplotlib"))
_mpl_cache.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(_mpl_cache))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm
import numpy as np

from models.scan import ScanModel

logger = logging.getLogger(__name__)


def _prefix_from_fb_path(fb_path: Path) -> str:
    name = fb_path.name
    return name[:-len("-fb.dat")] if name.endswith("-fb.dat") else fb_path.stem


def _load_scan_model(meta_path: Path | None) -> ScanModel | None:
    if meta_path is None or not meta_path.exists():
        return None

    with meta_path.open("r") as f:
        return ScanModel().from_dict(json.load(f))


def _find_meta_path(fb_path: Path, meta_path: str | None = None) -> Path | None:
    if meta_path:
        return Path(meta_path).expanduser()

    prefix = _prefix_from_fb_path(fb_path)
    candidate = fb_path.with_name(f"{prefix}-meta.json")
    return candidate if candidate.exists() else None


def _resolve_metadata(scan_model: ScanModel | None, spectral_resolution: int | None, dtype: str | None):
    filter_bank = getattr(scan_model, "filter_bank", None) if scan_model is not None else None
    resolved_spectral_resolution = spectral_resolution or getattr(scan_model, "spectral_resolution", None)
    resolved_dtype = dtype or "float32"
    temporal_ms = getattr(filter_bank, "temporal_resolution", None)

    if not resolved_spectral_resolution or int(resolved_spectral_resolution) <= 0:
        raise ValueError("spectral_resolution is required. Provide scan metadata or pass --spectral-resolution.")

    return int(resolved_spectral_resolution), np.dtype(resolved_dtype), temporal_ms


def load_filterbank_dat(
    fb_path: str,
    meta_path: str | None = None,
    spectral_resolution: int | None = None,
    dtype: str | None = None,
) -> tuple[np.ndarray, ScanModel | None, float | None]:
    """Load a raw project filterbank dat file into a rows x channels array."""

    path = Path(fb_path).expanduser()
    scan_model = _load_scan_model(_find_meta_path(path, meta_path))
    nchans, data_dtype, temporal_ms = _resolve_metadata(scan_model, spectral_resolution, dtype)

    data = np.fromfile(path, dtype=data_dtype)
    if data.size == 0:
        raise ValueError(f"Filterbank file is empty: {path}")

    complete_values = (data.size // nchans) * nchans
    if complete_values != data.size:
        logger.warning(
            "Dropping %d trailing values from %s because they do not make a complete %d-channel row.",
            data.size - complete_values,
            path,
            nchans,
        )
        data = data[:complete_values]

    if data.size == 0:
        raise ValueError(f"Filterbank file does not contain a complete {nchans}-channel row: {path}")

    return data.reshape(-1, nchans), scan_model, temporal_ms


def _robust_limits(data: np.ndarray, log_scale: bool) -> tuple[float, float, bool]:
    finite = data[np.isfinite(data)]
    finite = finite[finite > 0] if log_scale else finite
    if finite.size == 0:
        return 0.0, 1.0, False

    vmin, vmax = np.percentile(finite, [2, 98])
    if not np.isfinite(vmin) or not np.isfinite(vmax) or vmin == vmax:
        vmin, vmax = float(np.min(finite)), float(np.max(finite))
    if log_scale:
        vmin = max(vmin, np.finfo(float).tiny)
    if vmin == vmax:
        vmax = vmin + 1.0
    return float(vmin), float(vmax), log_scale


def _dc_channel_summary(data: np.ndarray) -> tuple[int, np.ndarray, np.ndarray]:
    centre = data.shape[1] // 2
    local_start = max(0, centre - 4)
    local_end = min(data.shape[1], centre + 5)
    centre_trace = data[:, centre].astype(np.float64)
    local_median = np.median(data[:, local_start:local_end].astype(np.float64), axis=1)
    return centre, centre_trace, local_median


def save_quicklook(
    fb_path: str,
    output_path: str | None = None,
    meta_path: str | None = None,
    spectral_resolution: int | None = None,
    dtype: str | None = None,
    max_rows: int | None = None,
    log_scale: bool = True,
) -> Path:
    """Create a PNG quicklook plot for a project filterbank dat file."""

    path = Path(fb_path).expanduser()
    data, scan_model, temporal_ms = load_filterbank_dat(path, meta_path, spectral_resolution, dtype)
    if max_rows is not None and max_rows > 0 and data.shape[0] > max_rows:
        row_idx = np.linspace(0, data.shape[0] - 1, max_rows).astype(int)
        display_data = data[row_idx]
        row_note = f"showing {max_rows:,} sampled rows of {data.shape[0]:,}"
    else:
        display_data = data
        row_note = f"{data.shape[0]:,} rows"

    display_data = display_data.astype(np.float64, copy=False)
    centre, centre_trace, local_median = _dc_channel_summary(data)
    mean_bandpass = np.mean(data.astype(np.float64), axis=0)
    channel_std = np.std(data.astype(np.float64), axis=0)

    vmin, vmax, use_log_scale = _robust_limits(display_data, log_scale)
    norm = LogNorm(vmin=vmin, vmax=vmax) if use_log_scale else None

    title_parts = [path.name, row_note, f"{data.shape[1]:,} channels"]
    if temporal_ms is not None:
        title_parts.append(f"{temporal_ms:g} ms")
    if scan_model is not None and getattr(scan_model, "scan_id", None):
        title_parts.append(scan_model.scan_id)

    fig, axes = plt.subplots(
        3,
        1,
        figsize=(13, 9),
        gridspec_kw={"height_ratios": [4.8, 1.5, 1.5]},
        constrained_layout=True,
    )

    im = axes[0].imshow(
        display_data,
        aspect="auto",
        origin="lower",
        interpolation="nearest",
        cmap="viridis",
        norm=norm,
        vmin=None if use_log_scale else vmin,
        vmax=None if use_log_scale else vmax,
    )
    axes[0].axvline(centre, color="white", linestyle=":", linewidth=1.0, alpha=0.8)
    axes[0].set_title(" | ".join(title_parts), fontsize=10)
    axes[0].set_ylabel("Time row")
    axes[0].set_xlabel("Frequency channel")
    fig.colorbar(im, ax=axes[0], label="Power")

    axes[1].plot(mean_bandpass, linewidth=0.9, label="Mean bandpass")
    axes[1].axvline(centre, color="tab:red", linestyle=":", linewidth=1.0, label="DC channel")
    axes[1].set_ylabel("Mean power")
    axes[1].set_xlabel("Frequency channel")
    axes[1].legend(loc="upper right", fontsize=8)

    axes[2].plot(centre_trace, linewidth=0.7, label="DC channel")
    axes[2].plot(local_median, linewidth=0.7, label="Median of centre +/-4 channels")
    axes[2].set_ylabel("Power")
    axes[2].set_xlabel("Time row")
    axes[2].legend(loc="upper right", fontsize=8)

    dc_ratio = np.median(centre_trace / np.maximum(local_median, np.finfo(float).tiny))
    channel_std_ratio = channel_std[centre] / max(np.median(channel_std), np.finfo(float).tiny)
    fig.suptitle(
        f"Filterbank quicklook: DC median ratio {dc_ratio:.3g}, DC std ratio {channel_std_ratio:.3g}",
        fontsize=11,
    )

    out = Path(output_path).expanduser() if output_path else path.with_name(f"{_prefix_from_fb_path(path)}-fb-quicklook.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return out


def init_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a quicklook PNG for a DMD2000 *-fb.dat filterbank file.")
    parser.add_argument("fb_dat", help="Path to the *-fb.dat file to visualise.")
    parser.add_argument("-m", "--meta", help="Optional path to the matching *-meta.json file.")
    parser.add_argument("-o", "--output", help="Output PNG path. Defaults to *-fb-quicklook.png next to the dat file.")
    parser.add_argument("--spectral-resolution", type=int, help="Number of frequency channels if metadata is unavailable.")
    parser.add_argument("--dtype", choices=["uint8", "uint16", "float32", "float64"], help="Filterbank sample dtype if metadata is unavailable.")
    parser.add_argument("--max-rows", type=int, default=5000, help="Maximum rows to draw in the waterfall; sampled evenly if needed.")
    parser.add_argument("--linear", action="store_true", help="Use linear colour scaling instead of log scaling.")
    return parser.parse_args()


def main() -> None:
    args = init_args()
    output = save_quicklook(
        fb_path=args.fb_dat,
        output_path=args.output,
        meta_path=args.meta,
        spectral_resolution=args.spectral_resolution,
        dtype=args.dtype,
        max_rows=args.max_rows,
        log_scale=not args.linear,
    )
    print(output)


if __name__ == "__main__":
    main()
