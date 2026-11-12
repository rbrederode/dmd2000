"""Shared, non-mutating spectrum statistics, independent of pipeline/plot state.

Callers select their signal and line-free noise channels. Estimator and numerical
floor policies are explicit so equal samples and policies give equal metrics.
Brightness and spectrum power use the existing 10-log ratio convention.
Profile helpers share pipeline QA's peak-exclusion selection and median/MAD default.
"""
from dataclasses import dataclass

import numpy as np

NOISE_ESTIMATORS = ("mean_rms", "median_mad")


@dataclass(frozen=True)
class NoiseStatistics:
    level: float
    scatter: float
    sample_count: int
    estimator: str


@dataclass(frozen=True)
class PeakSNR:
    signal: float
    noise: float
    linear: float
    db: float


def estimate_noise(samples, *, estimator="median_mad"):
    """Measure finite, unmasked 1D noise samples without modifying them.

    mean_rms: mean level and population RMS scatter about that mean (ddof=0).
    median_mad: median level and 1.4826 * median absolute deviation, a robust
    Gaussian-equivalent scatter estimate, not the literal sample RMS.
    No clipping, region selection, smoothing or minimum-channel policy is added.
    """
    if estimator not in NOISE_ESTIMATORS:
        raise ValueError(f"Noise estimator must be one of {NOISE_ESTIMATORS}")
    values = np.ma.asarray(samples, dtype=float)
    if values.ndim != 1:
        raise ValueError("Noise samples must be one-dimensional")
    values = np.ma.masked_invalid(values).compressed()
    if not values.size:
        raise ValueError("Noise statistics require finite, unmasked samples")
    if estimator == "mean_rms":
        level = float(np.mean(values))
        scatter = float(np.sqrt(np.mean((values - level)**2)))
    else:
        level = float(np.median(values))
        scatter = float(1.4826 * np.median(np.abs(values - level)))
    return NoiseStatistics(level, scatter, int(values.size), estimator)


def peak_snr(peak, level, scatter, *, minimum=None):
    """Peak above the noise level divided by its scatter, and 10 log10(ratio).

    With no minimum, nonpositive signal/scatter is undefined and raises ValueError.
    QA can explicitly supply its legacy positive floor before taking logarithms.
    dB uses a difference of logs to avoid overflow of the linear ratio. The
    linear ratio may be inf for extreme inputs; JSON callers must handle that.
    """
    if not np.all(np.isfinite([peak, level, scatter])) or scatter < 0:
        raise ValueError("Peak and noise statistics must be finite; scatter cannot be negative")
    signal, noise = float(peak - level), float(scatter)
    if minimum is not None:
        if not np.isfinite(minimum) or minimum <= 0:
            raise ValueError("The numerical floor must be finite and positive")
        signal, noise = max(signal, minimum), max(noise, minimum)
    if signal <= 0 or noise <= 0:
        raise ValueError("SNR requires positive peak above the noise level and positive scatter")
    with np.errstate(over="ignore"):
        linear = float(np.divide(signal, noise))
    db = float(10 * (np.log10(signal) - np.log10(noise)))
    return PeakSNR(float(signal), float(noise), linear, db)



def select_qa_channels(signal, *, window_frac=.2, smooth_window=10):
    """Pipeline QA's exact native-channel peak and noise selection, without mutation.

    Masks/nonfinite samples are excluded without compressing the channel grid.
    Smooth only to locate the peak; noise and peak amplitudes use original data.
    """
    values = np.ma.masked_invalid(np.ma.asarray(signal, dtype=float))
    if values.ndim != 1:
        raise ValueError("Spectrum must be one-dimensional")
    if isinstance(window_frac, bool) or not isinstance(window_frac, (int, float)) or not np.isfinite(window_frac) or not 0 < window_frac < 1:
        raise ValueError("window_frac must be a finite number between zero and one")
    if isinstance(smooth_window, bool) or not isinstance(smooth_window, (int, np.integer)) or smooth_window < 1:
        raise ValueError("smooth_window must be a positive integer")
    signal = values.filled(np.nan)
    valid = ~np.ma.getmaskarray(values)
    channels = len(signal)
    if np.count_nonzero(valid) < 3:
        raise ValueError("QA requires at least three usable channels")
    smooth_window = min(int(smooth_window), channels if channels % 2 == 1 else channels - 1)
    if smooth_window % 2 == 0:
        smooth_window += 1

    if smooth_window > 1:
        kernel = np.ones(smooth_window, dtype=np.float64)
        smoothed_sum = np.convolve(np.where(valid, signal, 0.0), kernel, mode="same")
        smoothed_count = np.convolve(valid.astype(np.float64), kernel, mode="same")
        smoothed_signal = np.divide(
            smoothed_sum,
            smoothed_count,
            out=np.full(signal.shape, -np.inf, dtype=np.float64),
            where=smoothed_count > 0,
        )
    else:
        smoothed_signal = np.where(valid, signal, -np.inf)

    # A flagged channel cannot be selected merely because valid neighbours
    # make its smoothed value large.
    smoothed_signal[~valid] = -np.inf

    peak_bin = int(np.argmax(smoothed_signal))

    # Estimate baseline using all but a window around the peak
    window_width = max(3, int(window_frac * channels))
    half_width = window_width // 2
    exclude_start = max(0, peak_bin - half_width)
    exclude_end = min(channels, peak_bin + half_width + 1)
    noise_mask = valid.copy()
    noise_mask[exclude_start:exclude_end] = False
    return dict(valid=valid, smoothed_signal=smoothed_signal, peak_bin=peak_bin, noise_mask=noise_mask,
                exclude_start=exclude_start, exclude_end=exclude_end)


def estimate_profile_noise(velocity, brightness, vmin, vmax, noise_ranges=None,
                           sigma=3., *, noise_estimator="median_mad", window_frac=.2,
                           smooth_window=10, noise_selection="qa_peak_exclusion"):
    """Use pipeline QA selection by default; explicit noise windows remain supported.

    Decreasing velocity is native increasing-frequency channel order. Within the
    requested range retain masked edges and gaps in the channel grid. Smoothing
    and exclusion widths use this restricted grid, as QA does for its input.
    Legacy positive-edge selection is an explicit option.
    """
    if noise_estimator not in NOISE_ESTIMATORS:
        raise ValueError(f"Noise estimator must be one of {NOISE_ESTIMATORS}")
    if noise_selection not in ("qa_peak_exclusion", "positive_edge"):
        raise ValueError("Unknown noise selection policy")
    if not np.all(np.isfinite([vmin, vmax, sigma])) or vmin >= vmax or sigma <= 0:
        raise ValueError("Noise limits and sigma must be finite; sigma positive")
    v = np.ma.asarray(velocity, dtype=float).filled(np.nan)
    t = np.ma.asarray(brightness, dtype=float).filled(np.nan)
    if v.ndim != 1 or v.shape != t.shape:
        raise ValueError("Profile coordinates and powers must be matching one-dimensional arrays")
    order = np.argsort(-v, kind="stable")
    v, t = v[order], t[order]
    finite = np.isfinite(v) & np.isfinite(t)
    visible = np.isfinite(v) & (v >= vmin) & (v <= vmax)
    result = dict(status="measured", sigma_multiplier=float(sigma), noise_estimator=noise_estimator,
                  noise_level_k=None, noise_rms_k=None, threshold_k=None, noise_sample_count=0,
                  noise_ranges_kms=[], selected_peak_value=None, window_frac=float(window_frac),
                  smooth_window=int(smooth_window),
                  noise_window_selection="explicit" if noise_ranges else noise_selection)
    if np.count_nonzero(finite & visible) < 3:
        result["status"] = "insufficient_noise_channels"
        return result
    displayed = np.flatnonzero(visible)
    selection = select_qa_channels(np.ma.array(t[displayed], mask=~finite[displayed]),
                                   window_frac=window_frac, smooth_window=smooth_window)
    result["selected_peak_value"] = float(t[displayed[selection["peak_bin"]]])
    if noise_ranges:
        noise = np.zeros(len(v), dtype=bool)
        for low, high in noise_ranges:
            if not np.all(np.isfinite([low, high])) or low >= high:
                raise ValueError("Noise windows must be finite with minimum < maximum")
            noise |= (v >= low) & (v <= high)
        result["noise_ranges_kms"] = [[float(low), float(high)] for low, high in noise_ranges]
        noise &= finite
    elif noise_selection == "positive_edge":
        indices = np.flatnonzero(finite & visible & (v > 0))
        count = int(np.ceil(len(indices)*.2))
        noise = np.zeros(len(v), dtype=bool)
        if count:
            chosen = indices[:count]
            noise[chosen] = True
            result["noise_ranges_kms"] = [[float(v[chosen[-1]]), float(v[chosen[0]])]]
    else:
        noise = np.zeros(len(v), dtype=bool)
        noise[displayed] = selection["noise_mask"]
        for side in (np.arange(len(displayed)) < selection["exclude_start"],
                     np.arange(len(displayed)) >= selection["exclude_end"]):
            points = v[displayed[selection["noise_mask"] & side]]
            if len(points):
                result["noise_ranges_kms"].append([float(points.min()), float(points.max())])
        result["noise_ranges_kms"].sort()
    if noise_selection == "positive_edge":
        result["selected_peak_value"] = float(np.max(t[finite & visible]))
    result["noise_sample_count"] = int(noise.sum())
    minimum = 10 if noise_ranges or noise_selection == "positive_edge" else 1
    if noise.sum() < minimum:
        result["status"] = "insufficient_noise_channels"
        return result
    stats = estimate_noise(t[noise], estimator=noise_estimator)
    result.update(noise_level_k=stats.level, noise_rms_k=stats.scatter,
                  threshold_k=float(stats.level + sigma*stats.scatter))
    if stats.scatter <= 0:
        result["status"] = "zero_noise_rms"  # Retain the legacy metadata status key.
    return result


def observation_noise_metrics(velocity, brightness, vmin, vmax, noise_ranges=None, *,
                              noise_estimator="median_mad", window_frac=.2, smooth_window=10,
                              noise_selection="qa_peak_exclusion"):
    """Pipeline-selected native peak above the noise level divided by scatter.

    Median/MAD and QA peak exclusion are defaults. Mean/RMS and the former
    positive-edge selection remain explicit options, using the same estimators.
    Degenerate profiles are unavailable rather than assigned QA's startup floor.
    """
    noise = estimate_profile_noise(velocity, brightness, vmin, vmax, noise_ranges,
                                   noise_estimator=noise_estimator, window_frac=window_frac,
                                   smooth_window=smooth_window, noise_selection=noise_selection)
    result = {key: noise[key] for key in ("noise_level_k", "noise_rms_k", "noise_sample_count",
                                         "noise_ranges_kms", "noise_window_selection", "noise_estimator",
                                         "window_frac", "smooth_window")}
    result.update(status="unavailable", peak_above_noise_floor=None, peak_snr=None,
                  peak_snr_db=None, noise_rms_db_relative_to_peak=None,
                  signal_range_kms=[float(vmin), float(vmax)],
                  db_convention="10 log10 of power-like brightness ratios",
                  db_reference=f"pipeline-selected peak above the {'mean' if noise_estimator == 'mean_rms' else 'median'} noise floor",
                  amplitude_unit="scaled observation plot units")
    if noise["noise_rms_k"] is None or noise["noise_rms_k"] <= 0:
        result["status"] = noise["status"]
        return result
    peak = noise["selected_peak_value"]
    if peak is None or peak <= noise["noise_level_k"]:
        result["status"] = "no_positive_peak"
        return result
    snr = peak_snr(peak, noise["noise_level_k"], noise["noise_rms_k"])
    result.update(status="measured", peak_above_noise_floor=snr.signal,
                  peak_snr=snr.linear if np.isfinite(snr.linear) else None,
                  peak_snr_db=snr.db, noise_rms_db_relative_to_peak=-snr.db)
    return result
