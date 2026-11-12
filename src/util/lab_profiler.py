"""Gaussian LAB extraction, observation fitting and predicted longitude–velocity maps.

Run ``python src/util/lab_profiler.py --help`` from the repository root.
The cube supplies LSR velocities and brightness temperatures; no additional
Earth-motion correction is applied. Profile/sweep beam FWHM is the Gaussian
averaging width. In --lv mode it is the requested final beam, corrected for
the nominal native LAB resolution. All widths use Galactic coordinate degrees.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import json
from pathlib import Path
import sys

import numpy as np
from astropy import units as u
from astropy.io import fits

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from util.convert import select_baseline_windows
from sdp.spectral_baseline import subtract_baseline as subtract_spectrum_baseline
from sdp.spectral_metrics import (
    NOISE_ESTIMATORS, estimate_noise, peak_snr,
    estimate_profile_noise, observation_noise_metrics,
)
from util.lv_plot import reference_lv_colormap

from obs.obs_pointing import (
    JODRELL_BANK_LOCATION,
    add_pointing_arguments,
    resolve_pointing_context as resolve_profile_context,
)


class LABGaussianExtractor:
    """Read a Galactic CAR LAB cube and average spectra with a Gaussian beam.

    ``extract(l, b, fwhm)`` accepts degrees and returns velocity (km/s LSR)
    and brightness (K). Blank pixels are excluded separately per channel.
    Weights extend three sigma in each coordinate and use fractional pixel
    offsets. This preserves the original coordinate-plane Gaussian method;
    it is not a spherical convolution or a correction for LAB's native beam.
    Use as a context manager to release the memory-mapped cube after a sweep.
    """

    def __init__(self, fits_file):
        self.path = Path(fits_file).expanduser()
        self._hdul = fits.open(self.path, do_not_scale_image_data=True, memmap=True)
        try:
            self.header = h = self._hdul[0].header
            self.data = self._hdul[0].data
            if self.data is None or self.data.ndim != 3:
                raise ValueError("Expected a 3D LAB FITS cube")
            if h.get("CTYPE1", "").strip().upper() != "GLON-CAR" or h.get("CTYPE2", "").strip().upper() != "GLAT-CAR":
                raise ValueError("Expected Galactic CAR longitude/latitude cube axes")
            if "LSR" not in h.get("CTYPE3", "").upper():
                raise ValueError("Expected an LSR velocity axis")
            self.nvel, self.nlat, self.nlon = self.data.shape
            self.l0, self.dl, self.lref = h["CRVAL1"], h["CDELT1"], h["CRPIX1"]
            self.b0, self.db, self.bref = h["CRVAL2"], h["CDELT2"], h["CRPIX2"]
            if not np.all(np.isfinite([self.dl, self.db])) or self.dl == 0 or self.db == 0:
                raise ValueError("Spatial pixel increments must be finite and nonzero")
            # LAB includes both endpoints of the 360-degree longitude axis.
            self.period = round(360 / abs(self.dl))
            if self.period < 1 or not np.isclose(self.period * abs(self.dl), 360) or self.nlon not in (self.period, self.period + 1):
                raise ValueError("Expected a full 360-degree longitude grid")
            velocity_unit = u.Unit(h.get("CUNIT3", "m/s").strip().lower())
            self.velocity = ((h["CRVAL3"] + (np.arange(self.nvel) - h["CRPIX3"] + 1) * h["CDELT3"]) * velocity_unit).to_value(u.km / u.s)
            self.temperature_factor = u.Unit(h.get("BUNIT", "K")).to(u.K)
            self.bzero, self.bscale, self.blank = h.get("BZERO", 0.), h.get("BSCALE", 1.), h.get("BLANK")
        except Exception:
            self.close()
            raise

    def close(self):
        self.data = None
        self._hdul.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def extract(self, l, b, fwhm):
        if not np.all(np.isfinite([l, b, fwhm])) or fwhm <= 0 or not -90 <= b <= 90:
            raise ValueError("Coordinates and FWHM must be finite; latitude -90..90 and FWHM positive")
        sigma = fwhm / np.sqrt(8 * np.log(2))
        xc = ((l - self.l0) / self.dl + self.lref - 1) % self.period
        yc = (b - self.b0) / self.db + self.bref - 1
        if not 0 <= yc <= self.nlat - 1:
            raise ValueError("Latitude is outside the FITS range")
        ix, iy = int(round(xc)), int(round(yc))
        rx = min(int(np.ceil(3 * sigma / abs(self.dl))) + 1, self.period // 2)
        ry = min(int(np.ceil(3 * sigma / abs(self.db))) + 1, self.nlat)
        xi = np.unique(np.arange(ix - rx, ix + rx + 1) % self.period)
        yi = np.arange(max(0, iy - ry), min(self.nlat, iy + ry + 1))
        dx = ((xi - xc + self.period / 2) % self.period - self.period / 2) * self.dl
        dy = (yi - yc) * self.db
        weights = np.exp(-0.5 * ((dx[None, :] / sigma)**2 + (dy[:, None] / sigma)**2))
        data = self.data[:, yi[:, None], xi[None, :]].astype(float)
        if self.blank is not None:
            data[data == self.blank] = np.nan
        data = (data * self.bscale + self.bzero) * self.temperature_factor
        valid = np.isfinite(data)
        denominator = np.sum(np.where(valid, weights, 0), axis=(1, 2))
        numerator = np.sum(np.where(valid, data, 0) * weights, axis=(1, 2))
        spectrum = np.full(self.nvel, np.nan)
        np.divide(numerator, denominator, out=spectrum, where=denominator > 0)
        return self.velocity.copy(), spectrum


def load_observation(path, target_id, freq_scan):
    """Select both identifiers in an Observation Velocity CSV; sort by velocity."""
    samples = []
    with Path(path).expanduser().open(newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"target_id", "freq_scan", "velocity_km_s", "calibrated_power"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f"Observation CSV requires columns: {', '.join(sorted(required))}")
        for row in reader:
            if row["target_id"] != str(target_id) or row["freq_scan"] != str(freq_scan):
                continue
            try:
                sample = float(row["velocity_km_s"]), float(row["calibrated_power"])
            except (TypeError, ValueError):
                continue
            if np.all(np.isfinite(sample)):
                samples.append(sample)
    if len(samples) < 10:
        raise ValueError(f"Fewer than 10 populated samples for target_id={target_id}, freq_scan={freq_scan}")
    data = np.array(sorted(samples))
    if np.any(np.diff(data[:, 0]) <= 0):
        raise ValueError("Selected observation contains duplicate velocity channels")
    return data[:, 0], data[:, 1]


def subtract_baseline(velocity, power, method="linear", fit_mask=None, baseline_windows=None):
    """Shared baseline subtraction, plus the profiler's positive-peak check.

    The helper preserves amplitudes; LAB peak scaling happens only in the plot.
    Linear subtraction is the default for both the Python API and CLI.
    """
    if baseline_windows:
        if method not in ("linear", "constant"):
            raise ValueError("--baseline-window requires a linear or constant observation baseline")
        # Baseline windows are independent of the LAB scoring interval/overlap.
        fit_mask = np.zeros(np.shape(velocity), dtype=bool)
        for low, high in baseline_windows:
            if not np.all(np.isfinite([low, high])) or low >= high:
                raise ValueError("Baseline windows must be finite with minimum < maximum")
            fit_mask |= (velocity >= low) & (velocity <= high)
    corrected, baseline = subtract_spectrum_baseline(velocity, power, method, fit_mask,
                                                    fit_line_free=bool(baseline_windows))
    if not np.any(np.isfinite(corrected)) or np.nanmax(corrected) <= 0:
        raise ValueError("Observation has no positive peak after baseline subtraction")
    return corrected, baseline


def match_channels(velocity, temperature):
    """Average adjacent LAB pairs, dropping the leading channel for odd cubes.

    The 891-channel LAB cube produces 445 pairs after dropping its first channel.
    Interpolation onto observation centres is performed separately for scoring.
    """
    start = len(velocity) % 2
    if len(velocity) - start < 2:
        raise ValueError("At least two LAB channels are required for pair averaging")
    return (np.asarray(velocity)[start:].reshape(-1, 2).mean(axis=1),
            np.asarray(temperature)[start:].reshape(-1, 2).mean(axis=1))


def range_values(value):
    try:
        parts = value.strip().strip("[]").split(",")
        minimum, maximum, step = map(float, parts)
    except ValueError as exc:
        raise ValueError("Ranges must be [minimum offset,maximum offset,step]") from exc
    if not np.all(np.isfinite([minimum, maximum, step])) or step <= 0 or maximum < minimum:
        raise ValueError("Range must be finite with positive step and maximum >= minimum")
    count = int(np.floor((maximum - minimum) / step + 1e-9)) + 1
    if count > 10000:
        raise ValueError("Range has more than 10000 points")
    return minimum + step * np.arange(count)


@dataclass
class FitResult:
    longitude: float
    latitude: float
    lon_offset: float
    lat_offset: float
    rms: float
    correlation: float
    velocity: np.ndarray
    temperature: np.ndarray


def compare_spectra(velocity, corrected, reference_velocity, temperature, vmin, vmax):
    """Score normalized spectral shapes on common observation channel centres."""
    order = np.argsort(reference_velocity)
    reference_velocity, temperature = reference_velocity[order], temperature[order]
    reference = np.interp(velocity, reference_velocity, temperature, left=np.nan, right=np.nan)
    valid = np.isfinite(reference) & (velocity >= vmin) & (velocity <= vmax)
    if valid.sum() < 10:
        return np.inf, np.nan
    observed, lab = corrected[valid], reference[valid]
    if max(observed) <= 0 or max(lab) <= 0:
        return np.inf, np.nan
    observed, lab = observed / max(observed), lab / max(lab)
    mask = (observed > .01) & (lab > .01)
    if mask.sum() < 10:
        return np.inf, np.nan
    rms = float(np.sqrt(np.mean((observed[mask] - lab[mask])**2)))
    correlation = float(np.corrcoef(observed[mask], lab[mask])[0, 1]) if np.std(observed[mask]) > 0 and np.std(lab[mask]) > 0 else np.nan
    return rms, correlation




def estimate_terminal_velocity(velocity, brightness, vmin, vmax, noise_ranges=None,
                               sigma=3., min_channels=3, *, noise_estimator="median_mad",
                               window_frac=.2, smooth_window=10, noise_selection="qa_peak_exclusion"):
    """Find the rightmost positive emission edge above the noise level + N scatter.

    Default noise selection and median/MAD match pipeline QA. Explicit noise
    windows and mean/RMS remain available. Search only the displayed positive interval. Reject
    short above-threshold runs and do not bridge missing channels. Interpolate
    the final falling crossing; report a lower bound if coverage ends first.
    The default scatter is 1.4826 * MAD about the median baseline. This is a threshold edge, not an optical-depth/dispersion model.
    """
    if not np.all(np.isfinite([vmin, vmax, sigma])) or vmin >= vmax or sigma <= 0 or min_channels < 1:
        raise ValueError("Terminal limits and sigma must be finite; sigma and minimum channels positive")
    velocity = np.ma.asarray(velocity, dtype=float).filled(np.nan)
    brightness = np.ma.asarray(brightness, dtype=float).filled(np.nan)
    if velocity.ndim != 1 or velocity.shape != brightness.shape:
        raise ValueError("Profile coordinates and powers must be matching one-dimensional arrays")
    order = np.argsort(velocity)
    velocity, brightness = velocity[order], brightness[order]
    finite = np.isfinite(velocity) & np.isfinite(brightness)
    search = finite & (velocity > 0) & (velocity >= vmin) & (velocity <= vmax)
    noise = estimate_profile_noise(velocity, brightness, vmin, vmax, noise_ranges, sigma,
                                   noise_estimator=noise_estimator, window_frac=window_frac,
                                   smooth_window=smooth_window, noise_selection=noise_selection)
    result = {**noise, "status": "not_detected", "velocity_lsr_kms": None,
              "minimum_channels": int(min_channels), "search_range_kms": [float(max(0, vmin)), float(vmax)]}
    if noise["status"] != "measured":
        result["status"] = noise["status"]
        return result
    threshold = noise["threshold_k"]
    indices = np.flatnonzero(search & (brightness > threshold))
    if not len(indices):
        return result
    spacings = np.diff(velocity[np.isfinite(velocity)])
    spacings = spacings[spacings > 0]
    max_gap = 1.5 * np.median(spacings) if len(spacings) else 0.
    breaks = (np.diff(indices) > 1) | (np.diff(velocity[indices]) > max_gap)
    runs = [run for run in np.split(indices, np.flatnonzero(breaks) + 1) if len(run) >= min_channels]
    if not runs:
        return result
    index = runs[-1][-1]
    result["last_above_threshold_velocity_kms"] = float(velocity[index])
    result["emission_run_channels"] = int(len(runs[-1]))
    following = index + 1
    if following >= len(velocity) or velocity[following] > vmax:
        result["status"] = "edge_limited"
    elif not finite[following] or velocity[following] - velocity[index] > max_gap:
        result["status"] = "gap_limited"
    else:
        fraction = (brightness[index] - threshold) / (brightness[index] - brightness[following])
        crossing = velocity[index] + fraction * (velocity[following] - velocity[index])
        result.update(status="detected", velocity_lsr_kms=float(crossing))
    return result


def estimate_connected_terminal_velocity(velocity, brightness, vmin, vmax, noise_ranges=None,
                                        sigma=3., min_channels=3, return_width=3.,
                                        threshold_reference=None, threshold_source="self", *,
                                        noise_estimator="median_mad", window_frac=.2, smooth_window=10,
                                        noise_selection="qa_peak_exclusion"):
    """Follow the dominant emission to a sustained below-threshold return.

    A smoothed guide locates the strongest supported emission run; crossings
    use original samples. Short dips are bridged until at least min_channels
    consecutive samples spanning return_width km/s are below threshold. Stop
    at that first confirmed return, never at a later detached emission island.
    A reference noise estimate allows both spectra to use one detection level
    in their common plotting units. This is a detection edge, not an erfc
    midpoint or an independently determined Galactic tangent-point velocity.
    """
    from scipy.ndimage import gaussian_filter1d, median_filter
    if (not np.all(np.isfinite([vmin, vmax, sigma, return_width])) or vmin >= vmax
            or sigma <= 0 or min_channels < 1 or return_width <= 0):
        raise ValueError("Connected-edge limits must be finite; sigma, channels and return width positive")
    noise = (estimate_profile_noise(velocity, brightness, vmin, vmax, noise_ranges, sigma, noise_estimator=noise_estimator,
                                    window_frac=window_frac, smooth_window=smooth_window, noise_selection=noise_selection)
             if threshold_reference is None else threshold_reference)
    keys = ("noise_level_k", "noise_rms_k", "threshold_k", "noise_sample_count", "noise_ranges_kms",
            "noise_window_selection", "sigma_multiplier")
    result = {key: noise[key] for key in keys}
    result["noise_estimator"] = noise.get("noise_estimator", "median_mad")
    result.update(status="not_detected", velocity_lsr_kms=None, minimum_channels=int(min_channels),
                  search_range_kms=[float(max(0, vmin)), float(vmax)],
                  threshold_source=threshold_source, return_width_kms=float(return_width),
                  edge_definition="first sustained below-threshold return after dominant positive emission")
    if noise["threshold_k"] is None or noise["noise_rms_k"] is None or noise["noise_rms_k"] <= 0:
        result["status"] = (noise["status"] if threshold_reference is None else "invalid_threshold_reference")
        return result
    v = np.ma.asarray(velocity, dtype=float).filled(np.nan)
    t = np.ma.asarray(brightness, dtype=float).filled(np.nan)
    order = np.argsort(v)
    v, t = v[order], t[order]
    finite = np.isfinite(v) & np.isfinite(t)
    search = finite & (v > 0) & (v >= vmin) & (v <= vmax)
    spacing = np.diff(v[np.isfinite(v)])
    spacing = spacing[spacing > 0]
    if not len(spacing):
        return result
    step = float(np.median(spacing))
    max_gap = 1.5*step
    indices = np.flatnonzero(search)
    if not len(indices):
        return result
    breaks = (np.diff(indices)>1) | (np.diff(v[indices])>max_gap)
    candidates = []
    threshold = result["threshold_k"]
    for segment in np.split(indices, np.flatnonzero(breaks)+1):
        guide = gaussian_filter1d(median_filter(t[segment], size=3, mode="nearest"),
                                   max(1., step)/step, mode="nearest")
        above = np.flatnonzero(t[segment] > threshold)
        runs = np.split(above, np.flatnonzero(np.diff(above)>1)+1)
        for run in runs:
            if len(run) >= min_channels:
                peak = run[np.argmax(guide[run])]
                candidates.append((float(guide[peak]), int(segment[peak]), len(run)))
    if not candidates:
        return result
    _, peak, run_channels = max(candidates)
    result.update(peak_velocity_kms=float(v[peak]), emission_run_channels=int(run_channels),
                  selection_smoothing_sigma_kms=max(1., step))
    last_above = peak
    below_start = None
    for index in range(peak+1, len(v)):
        if v[index] > vmax:
            break
        if not finite[index] or v[index]-v[index-1] > max_gap:
            result.update(status="gap_limited", last_above_threshold_velocity_kms=float(v[last_above]))
            return result
        if t[index] > threshold:
            last_above = index
            below_start = None
        else:
            if below_start is None:
                below_start = index
            count = index-below_start+1
            width = v[index]-v[below_start]
            if count >= min_channels and width >= return_width:
                before = below_start-1
                fraction = (t[before]-threshold)/(t[before]-t[below_start])
                crossing = v[before]+fraction*(v[below_start]-v[before])
                result.update(status="detected", velocity_lsr_kms=float(crossing),
                              last_above_threshold_velocity_kms=float(v[before]),
                              return_below_threshold_channels=int(count),
                              return_confirmed_width_kms=float(width))
                return result
    result.update(status="edge_limited", last_above_threshold_velocity_kms=float(v[last_above]))
    return result




def erfc_edge(velocity, baseline, amplitude, terminal, width):
    """Falling edge; width is the Gaussian dispersion in km/s."""
    from scipy.special import erfc
    return baseline + .5 * amplitude * erfc((np.asarray(velocity) - terminal) / (np.sqrt(2) * width))


def select_falling_wing(velocity, brightness, search_range):
    """Select a strong positive emission's falling wing on a smoothed guide.

    Smoothing is only for selection; the erfc fit uses original samples. Gaps
    split the guide into independent segments and are never interpolated over.
    This heuristic selects a dominant falling transition, not necessarily the
    most extreme weak component or a physical Galactic tangent-point edge.
    """
    from scipy.ndimage import gaussian_filter1d, median_filter
    low, high = search_range
    if not np.all(np.isfinite([low, high])) or low < 0 or low >= high:
        raise ValueError("Automatic erfc search requires finite limits with 0 <= minimum < maximum")
    result = dict(status="insufficient_channels", window_selection="automatic_falling_wing",
                  search_range_kms=[float(low), float(high)], velocity_lsr_kms=None)
    v, t = np.asarray(velocity), np.asarray(brightness)
    mask = np.isfinite(v) & np.isfinite(t) & (v >= low) & (v <= high)
    order = np.argsort(v[mask])
    v, t = v[mask][order], t[mask][order]
    if len(v) < 12 or np.any(np.diff(v) <= 0):
        return result
    step = float(np.median(np.diff(v)))
    segments = np.split(np.arange(len(v)), np.flatnonzero(np.diff(v) > 1.5*step)+1)
    candidates = []
    for segment in segments:
        if len(segment) < 12:
            continue
        x, y = v[segment], t[segment]
        smoothing_sigma = max(1., step)  # km/s, never narrower than one channel.
        smooth = gaussian_filter1d(median_filter(y, size=3, mode="nearest"),
                                   smoothing_sigma/step, mode="nearest")
        tail = max(3, int(np.ceil(len(y)*.2)))
        floor = float(np.median(y[-tail:]))
        q25, q75 = np.quantile(y[-tail:], [.25, .75])
        noise = float((q75-q25)/1.349)
        peak = int(np.argmax(smooth))
        contrast = float(smooth[peak]-floor)
        if contrast <= max(5*noise, np.finfo(float).eps*max(1., np.max(np.abs(y)))):
            continue
        gradient = np.gradient(smooth, x)
        edge = peak + int(np.argmin(gradient[peak:]))
        if edge <= peak or gradient[edge] >= 0:
            continue
        # An erfc derivative is Gaussian: estimate dispersion from the width
        # at one quarter of the maximum negative gradient.
        left = right = edge
        cutoff = gradient[edge]*.25
        while left > peak and gradient[left] < cutoff:
            left -= 1
        while right < len(x)-1 and gradient[right] < cutoff:
            right += 1
        width = max(3*step, float((x[right]-x[left])/(2*np.sqrt(2*np.log(4)))))
        selected = [max(float(x[0]), float(x[edge]-3.5*width)),
                    min(float(x[-1]), float(x[edge]+5*width))]
        candidates.append((contrast, dict(result, status="selected", selected_range_kms=selected,
            seed_velocity_kms=float(x[edge]), seed_width_kms=width,
            selection_smoothing_sigma_kms=smoothing_sigma,
            selection_guide="three-channel median then Gaussian; original channels used for fitting",
            segment_range_kms=[float(x[0]), float(x[-1])],
            selection_tail_level=floor, selection_tail_noise=noise)))
    if not candidates:
        return dict(result, status="no_falling_edge")
    return max(candidates, key=lambda item: item[0])[1]


def fit_terminal_erfc(velocity, brightness, fit_range=None, *, search_range=None):
    """Fit a manual or automatically selected positive wing in native units.

    The midpoint of the fitted step is a model edge, not a noise crossing.
    Formal covariance errors exclude baseline, beam and model uncertainties.
    """
    if fit_range is None:
        if search_range is None:
            raise ValueError("Automatic erfc fitting requires a positive search range")
        selection = select_falling_wing(velocity, brightness, search_range)
        if selection["status"] != "selected":
            return selection
        fit = fit_terminal_erfc(velocity, brightness, selection["selected_range_kms"])
        return {**selection, **fit, "window_selection": selection["window_selection"]}
    import warnings
    from scipy.optimize import curve_fit, OptimizeWarning
    low, high = fit_range
    if not np.all(np.isfinite([low, high])) or low < 0 or low >= high:
        raise ValueError("Erfc fit range must be finite with 0 <= minimum < maximum")
    result = dict(status="insufficient_channels", model="baseline + amplitude/2 * erfc((v-v_terminal)/(sqrt(2)*width))",
                  fit_range_kms=[float(low), float(high)], velocity_lsr_kms=None,
                  window_selection="manual")
    v, t = np.asarray(velocity), np.asarray(brightness)
    mask = np.isfinite(v) & np.isfinite(t) & (v >= low) & (v <= high)
    v, t = v[mask], t[mask]
    order = np.argsort(v)
    v, t = v[order], t[order]
    result["sample_count"] = len(v)
    if len(v) < 12 or len(np.unique(v)) != len(v):
        return result
    step = float(np.median(np.diff(v)))
    if np.any(np.diff(v) > 1.5 * step):
        result["status"] = "gap_in_fit_window"
        return result
    span = float(v[-1] - v[0])
    tail = max(3, len(t) // 5)
    baseline = float(np.median(t[-tail:]))
    amplitude = float(np.median(t[:tail]) - baseline)
    if amplitude <= 0:
        result["status"] = "no_falling_edge"
        return result
    midpoint = float(v[np.argmin(abs(t - baseline - amplitude / 2))])
    minimum_width = step / 2
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", OptimizeWarning)
            parameters, covariance = curve_fit(
                erfc_edge, v, t, p0=[baseline, amplitude, np.clip(midpoint, v[0]+step, v[-1]-step), max(step, span/10)],
                bounds=([-np.inf, 0, v[0], minimum_width], [np.inf, np.inf, v[-1], span]), maxfev=20000)
    except (RuntimeError, ValueError, OptimizeWarning):
        result["status"] = "fit_failed"
        return result
    base, amp, terminal, width = parameters
    if not np.all(np.isfinite(covariance)) or terminal - v[0] < width or v[-1] - terminal < 2*width or width <= minimum_width*1.01 or width >= span*.99:
        result["status"] = "unconstrained_edge"
        return result
    errors = np.sqrt(np.maximum(0, np.diag(covariance)))
    result.update(status="fitted", velocity_lsr_kms=float(terminal),
                  velocity_formal_error_kms=float(errors[2]), width_kms=float(width),
                  width_formal_error_kms=float(errors[3]), baseline=float(base), amplitude=float(amp),
                  residual_rms=float(np.sqrt(np.mean((t-erfc_edge(v, *parameters))**2))),
                  sampled_range_kms=[float(v[0]), float(v[-1])])
    return result


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    add_pointing_arguments(parser)
    parser.add_argument("--lon", type=float, help="Nominal Galactic longitude (degrees); use with --lat")
    parser.add_argument("--lat", type=float, help="Nominal Galactic latitude (degrees); use with --lon")
    parser.add_argument("--lab-fits", type=Path, default=Path.home() / "github/lab-fit/lab.fit")
    parser.add_argument("--beam", "--fwhm", type=float, default=5., help="Gaussian FWHM in degrees (default 5); --lv interprets this as the final beam")
    parser.add_argument("--file", type=Path, help="Observation Velocity CSV")
    parser.add_argument("--target-id", help="Observation target ID; required with --file")
    parser.add_argument("--freq-scan", help="Frequency scan ID; required with --file")
    parser.add_argument("--sweep", action="store_true", help="Fit Galactic offsets to the selected observation")
    parser.add_argument("--lv", action="store_true", help="Predict a longitude–velocity map at fixed latitude, using --lon-range offsets")
    parser.add_argument("--lv-style", choices=("reference", "inferno"), default="reference",
                        help="Predicted L–V appearance: white/blue with magenta contours (default reference), or original inferno")
    parser.add_argument("--lv-stretch", choices=("linear", "sqrt", "asinh"),
                        help="Colour mapping only; default asinh for reference, linear for inferno")
    parser.add_argument("--lv-brightness-min", type=float, help="Lower colour limit in K; default 0 for reference, data minimum for inferno")
    parser.add_argument("--lv-brightness-max", type=float, help="Upper colour limit in K; default visible data maximum")
    parser.add_argument("--lv-asinh-width", type=float, default=10., help="Asinh linear transition width in K (default 10)")
    parser.add_argument("--lv-contours", nargs="+", type=float, metavar="K",
                        help="Increasing brightness contour levels in K; reference defaults to 5 10 20 40 80")
    parser.add_argument("--lv-no-contours", action="store_true", help="Disable predicted L–V contours")
    parser.add_argument("--lv-longitude-order", choices=("increasing", "decreasing"),
                        help="Default decreasing with signed labels for reference, increasing for inferno")
    parser.add_argument("--lab-native-beam", type=float, default=.6,
                        help="Nominal LAB native FWHM in degrees for --lv final-beam convolution (default 0.6)")
    parser.add_argument("--lon-range", default="[-3,3,1]", help="Longitude offsets [min,max,step] in degrees for sweeps or --lv")
    parser.add_argument("--lat-range", default="[-3,3,1]", help="Latitude offsets [min,max,step] in degrees")
    parser.add_argument("--fit-vmin", type=float, default=-150.)
    parser.add_argument("--fit-vmax", type=float, default=150.)
    parser.add_argument("--observation-baseline", choices=("none", "constant", "linear"), default="linear",
                        help="Baseline model (default linear with automatic outer windows); --no-baseline-auto uses lower-envelope fitting")
    parser.add_argument("--baseline-window", nargs=2, type=float, action="append", metavar=("VMIN", "VMAX"),
                        help="Line-free observation baseline window (km/s); repeat for multiple windows. Uses all selected channels with symmetric outlier clipping, independently of --fit-vmin/--fit-vmax")
    parser.add_argument("--baseline-auto", action=argparse.BooleanOptionalAction, default=True,
                        help="Automatically select outer 20%% baseline windows on both sides within --vmin/--vmax (default enabled for linear/constant observation baselines). Explicit --baseline-window overrides this; --no-baseline-auto restores lower-envelope fitting")
    parser.add_argument("--vmin", type=float, default=-100., help="Plot minimum LSR velocity")
    parser.add_argument("--vmax", type=float, default=100., help="Plot maximum LSR velocity")
    parser.add_argument("--noise-estimator", choices=NOISE_ESTIMATORS, default="median_mad",
                        help="Noise estimator (default median_mad, matching pipeline QA)")
    parser.add_argument("--noise-window-frac", type=float, default=.2,
                        help="Fraction of native channels excluded around the smoothed peak (default 0.2)")
    parser.add_argument("--noise-smooth-window", type=int, default=10,
                        help="QA peak-location smoothing window (default 10, adjusted to odd)")
    parser.add_argument("--noise-selection", choices=("qa_peak_exclusion", "positive_edge"), default="qa_peak_exclusion",
                        help="QA peak exclusion by default; positive_edge explicitly restores legacy edge selection")
    parser.add_argument("--noise-range", nargs=2, type=float, action="append", metavar=("VMIN", "VMAX"),
                        help="Explicit LSR noise window (km/s); repeat for multiple windows. Default: pipeline QA peak exclusion")
    parser.add_argument("--terminal-source", choices=("auto", "observation", "lab"), default="auto",
                        help="Spectrum for terminal velocity; auto compares observation and LAB when available, otherwise LAB")
    parser.add_argument("--terminal-sigma", type=float, default=3., help="Threshold above the configured noise baseline in scatter units (default 3)")
    parser.add_argument("--terminal-min-channels", type=int, default=3,
                        help="Minimum consecutive channels above threshold (default 3; use 1 for the rightmost individual channel)")
    parser.add_argument("--no-terminal-velocity", action="store_true", help="Disable terminal-velocity estimation")
    parser.add_argument("--terminal-method", choices=("threshold", "connected", "erfc"), default="threshold",
                        help="Terminal estimator: threshold finds the rightmost run; connected follows the main wing using a shared observation threshold; erfc fits the transition midpoint")
    parser.add_argument("--terminal-return-width", type=float, default=3., metavar="KM_S",
                        help="For connected edges, require below-threshold samples spanning this velocity width (default 3 km/s), plus --terminal-min-channels samples")
    parser.add_argument("--terminal-fit-range", nargs=2, type=float, metavar=("VMIN", "VMAX"),
                        help="Manual erfc falling-wing window in km/s; omit to select automatically within the positive displayed range")
    parser.add_argument("--output", type=Path, help="CSV output path; PNG/JSON use the same stem")
    parser.add_argument("--display-only", action="store_true", help="Show a plot without saving files")
    return parser


def predict_lv(extractor, longitudes, latitude, beam, native_beam=.6):
    """Return native LSR channels and a (velocity, longitude) kelvin array.

    ``beam`` is the requested final Gaussian FWHM. The smoothing kernel is
    sqrt(beam**2 - native_beam**2), using a nominal native LAB resolution.
    Extraction retains the existing Galactic-coordinate-plane Gaussian model.
    No baseline subtraction, receiver simulation or amplitude scaling is used.
    """
    longitudes = np.asarray(longitudes, dtype=float)
    if longitudes.ndim != 1 or not len(longitudes) or not np.all(np.isfinite(longitudes)):
        raise ValueError("Predicted L–V longitudes must be a finite non-empty 1D array")
    if not np.all(np.isfinite([latitude, beam, native_beam])) or not -90 <= latitude <= 90 or native_beam < 0 or beam <= native_beam:
        raise ValueError("L–V latitude must be -90..90 and final beam must exceed the non-negative native LAB beam")
    kernel = np.sqrt(beam**2 - native_beam**2)
    columns = []
    velocity = None
    for index, longitude in enumerate(longitudes):
        channels, temperature = extractor.extract(float(longitude % 360), latitude, kernel)
        if velocity is None:
            velocity = np.asarray(channels)
        elif not np.array_equal(channels, velocity):
            raise ValueError("LAB cube returned inconsistent velocity channels")
        columns.append(temperature)
        if (index + 1) % max(1, len(longitudes) // 10) == 0 or index == len(longitudes) - 1:
            print(f"Predicted LAB profiles: {index + 1}/{len(longitudes)}")
    order = np.argsort(velocity)
    velocity = velocity[order]
    if len(velocity) < 2 or not np.all(np.isfinite(velocity)) or np.any(np.diff(velocity) <= 0):
        raise ValueError("LAB velocity channels must be finite and distinct")
    return velocity, np.column_stack(columns)[order]


def plot_predicted_lv(args, context, offsets):
    """Extract, display and optionally export a predicted LAB L–V diagram."""
    import matplotlib.pyplot as plt
    from matplotlib.colors import AsinhNorm, Normalize, PowerNorm
    from matplotlib.ticker import FuncFormatter, MaxNLocator

    longitudes = context.galactic.l.deg + offsets
    latitude = float(context.galactic.b.deg)
    with LABGaussianExtractor(args.lab_fits) as extractor:
        velocity, temperature = predict_lv(extractor, longitudes, latitude, args.beam, args.lab_native_beam)
    visible = (velocity >= args.vmin) & (velocity <= args.vmax)
    values = temperature[visible]
    if not np.any(np.isfinite(values)):
        raise ValueError("No finite LAB temperatures in the requested L–V velocity range")

    def bin_edges(centres, single_width=1.):
        if len(centres) == 1:
            return np.array([centres[0] - single_width / 2, centres[0] + single_width / 2])
        return np.r_[centres[0] - (centres[1] - centres[0]) / 2,
                     (centres[:-1] + centres[1:]) / 2,
                     centres[-1] + (centres[-1] - centres[-2]) / 2]

    reference_style = args.lv_style == "reference"
    stretch = args.lv_stretch or ("asinh" if reference_style else "linear")
    colour_min = args.lv_brightness_min if args.lv_brightness_min is not None else (0. if reference_style else float(np.nanmin(values)))
    colour_max = args.lv_brightness_max if args.lv_brightness_max is not None else float(np.nanmax(values))
    if args.lv_brightness_max is None and colour_max <= colour_min:
        colour_max = colour_min + max(abs(colour_min) * .01, 1e-6)
    if colour_max <= colour_min:
        raise ValueError("Upper L–V brightness limit must exceed the lower limit")
    if stretch == "asinh":
        norm = AsinhNorm(linear_width=args.lv_asinh_width, vmin=colour_min, vmax=colour_max, clip=True)
    elif stretch == "sqrt":
        norm = PowerNorm(gamma=.5, vmin=colour_min, vmax=colour_max, clip=True)
    else:
        norm = Normalize(vmin=colour_min, vmax=colour_max, clip=True)
    if reference_style:
        cmap = reference_lv_colormap()
    else:
        cmap = "inferno"
    fig, ax = plt.subplots(figsize=(13, 6), layout="constrained", facecolor="white")
    ax.set_facecolor("white")
    mesh = ax.pcolormesh(bin_edges(longitudes), bin_edges(velocity), np.ma.masked_invalid(temperature),
                         cmap=cmap, norm=norm, shading="flat", rasterized=True)
    requested_levels = args.lv_contours if args.lv_contours is not None else ([5., 10., 20., 40., 80.] if reference_style else [])
    contour_levels = []
    if not args.lv_no_contours and len(longitudes) > 1 and visible.sum() > 1:
        contour_levels = [float(level) for level in requested_levels if np.nanmin(values) < level < np.nanmax(values)]
        if contour_levels:
            contours = ax.contour(longitudes, velocity[visible], np.ma.masked_invalid(values),
                                  levels=contour_levels, colors="#c849b8", linewidths=.65, alpha=.8)
            ax.clabel(contours, inline=True, fontsize=7, fmt="%g K")
    label = f"{longitudes[0] % 360:.3f}° to {longitudes[-1] % 360:.3f}°"
    if longitudes[0] // 360 != longitudes[-1] // 360:
        label += " (across 0°)"
    longitude_order = args.lv_longitude_order or ("decreasing" if reference_style else "increasing")
    longitude_edges = bin_edges(longitudes)
    if np.isclose(longitude_edges[-1] - longitude_edges[0], 360, rtol=0, atol=1e-8):
        label = ("+180° to −180° (full coverage)" if longitude_order == "decreasing" else "−180° to +180° (full coverage)") if reference_style else "0° to 360° (full coverage)"
    ax.set(xlabel="Galactic longitude [degrees]", ylabel="LSR velocity [km/s]",
           ylim=(args.vmin, args.vmax), xlim=(bin_edges(longitudes)[0], bin_edges(longitudes)[-1]),
           title=f"Predicted LAB longitude–velocity | Gaussian beam FWHM={args.beam:g}°\n"
                 f"Galactic longitude {label}; latitude {latitude:+.3f}°")
    ax.xaxis.set_major_locator(MaxNLocator(nbins=10))
    def longitude_label(value, position):
        longitude = (value + 180) % 360 - 180 if reference_style else value % 360
        if reference_style and np.isclose(longitude, -180) and value > 0:
            longitude = 180
        return f"{longitude:g}°"
    ax.xaxis.set_major_formatter(FuncFormatter(longitude_label))
    if longitude_order == "decreasing":
        ax.invert_xaxis()
    ax.minorticks_on()
    ax.tick_params(which="both", direction="in", top=True, right=True)
    ax.axhline(0, color="grey" if reference_style else "white", linestyle=":", linewidth=.6, alpha=.6)
    colour_bar = fig.colorbar(mesh, ax=ax, label="Predicted H I brightness temperature [K]")
    if stretch == "asinh":
        from matplotlib.ticker import FormatStrFormatter
        ticks = sorted(set([colour_min, colour_max] + [float(level) for level in requested_levels if colour_min < level < colour_max]))
        colour_bar.set_ticks(ticks)
        colour_bar.ax.yaxis.set_major_formatter(FormatStrFormatter("%g"))
    if not args.display_only:
        prefix = args.output.expanduser().with_suffix("") if args.output else Path(args.output_dir or args.dir).expanduser() / f"{context.output_name}-lab-lv"
        prefix.parent.mkdir(parents=True, exist_ok=True)
        png_path, csv_path, json_path = (Path(f"{prefix}{suffix}") for suffix in (".png", ".csv", ".json"))
        fig.savefig(png_path, dpi=180)
        # Export all native velocity channels, not just the displayed interval.
        with csv_path.open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["galactic_longitude_deg", "galactic_latitude_deg", "velocity_lsr_kms", "brightness_K"])
            for column, longitude in enumerate(longitudes):
                writer.writerows((float(longitude % 360), latitude, float(v), float(t))
                                 for v, t in zip(velocity, temperature[:, column]))
        metadata = {**context.metadata(), "mode": "predicted_lv", "survey": "LAB Galactic CAR cube",
                    "lab_fits": str(args.lab_fits.expanduser().resolve()),
                    "final_beam_fwhm_deg": args.beam, "nominal_native_beam_fwhm_deg": args.lab_native_beam,
                    "smoothing_kernel_fwhm_deg": float(np.sqrt(args.beam**2 - args.lab_native_beam**2)),
                    "beam_method": "Gaussian in Galactic coordinate degrees; three sigma aperture; nominal native-beam quadrature correction",
                    "longitude_offsets_deg": offsets.tolist(), "longitudes_deg": (longitudes % 360).tolist(),
                    "unwrapped_longitudes_deg": longitudes.tolist(), "latitude_deg": latitude,
                    "velocity_frame": "LSR", "brightness_unit": "K", "colormap": mesh.get_cmap().name,
                    "lv_style": args.lv_style, "colour_stretch": stretch,
                    "colour_limits_k": [colour_min, colour_max], "asinh_width_k": args.lv_asinh_width if stretch == "asinh" else None,
                    "contour_levels_k": contour_levels, "longitude_order": longitude_order,
                    "longitude_labels": "signed" if reference_style else "unsigned",
                    "display_velocity_range_kms": [args.vmin, args.vmax],
                    "velocity_channels": len(velocity), "longitude_samples": len(longitudes),
                    "amplitude_scaling": "none", "baseline_subtraction": "none"}
        json_path.write_text(json.dumps(metadata, indent=2, allow_nan=False) + "\n")
        print(f"Saved: {png_path}, {csv_path}, {json_path}")
    if not args.no_show:
        plt.show()
    plt.close(fig)


def plot_result(result, args, context, observation=None):
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(result.velocity, result.temperature, color="black", linestyle="--", label="LAB Survey")
    noise_metrics = None
    noise_policy = dict(noise_estimator=args.noise_estimator, window_frac=args.noise_window_frac,
                        smooth_window=args.noise_smooth_window, noise_selection=args.noise_selection)
    if observation is not None:
        velocity, corrected, baseline = observation
        scaled = corrected * np.nanmax(result.temperature) / np.max(corrected)
        noise_metrics = observation_noise_metrics(velocity, scaled, args.vmin, args.vmax, args.noise_range, **noise_policy)
        baseline_label = "no baseline subtraction" if args.observation_baseline == "none" else f"{args.observation_baseline} baseline removed"
        if args.baseline_auto and args.baseline_window:
            baseline_label = f"auto {args.observation_baseline} baseline"
        ax.plot(velocity, scaled, color="red", label=f"Observation ({baseline_label}; scaled to LAB peak)")
    terminal = {"status": "disabled"}
    if not args.no_terminal_velocity and args.terminal_method == "erfc":
        terminal = {"method": "erfc", "spectra": {}}
        spectra = {"lab": (result.velocity, result.temperature, 1.)}
        if observation is not None:
            spectra["observation"] = (velocity, corrected, float(np.nanmax(result.temperature) / np.max(corrected)))
        for source, (edge_v, edge_t, scale) in spectra.items():
            if args.terminal_source not in ("auto", source):
                continue
            fit = fit_terminal_erfc(edge_v, edge_t, args.terminal_fit_range,
                                    search_range=[max(0., args.vmin), args.vmax])
            fit["amplitude_unit"] = "K" if source == "lab" else "observation input power units"
            terminal["spectra"][source] = fit
            if "selected_range_kms" in fit:
                low, high = fit["selected_range_kms"]
                print(f"Automatic erfc window ({source}): {low:.3f} to {high:.3f} km/s")
            if fit["status"] == "fitted":
                terminal_v = fit["velocity_lsr_kms"]
                colour = "tab:blue" if source == "lab" else "tab:orange"
                sample = np.linspace(*fit["sampled_range_kms"], 300)
                window_label = (f" (auto {sample[0]:.1f}–{sample[-1]:.1f} km/s)"
                                if fit["window_selection"] == "automatic_falling_wing" else "")
                ax.plot(sample, scale * erfc_edge(sample, fit["baseline"], fit["amplitude"], terminal_v, fit["width_kms"]),
                        color=colour, linewidth=2, label=f"{source.upper()} erfc wing fit{window_label}")
                ax.axvline(terminal_v, color=colour, linestyle=":",
                           label=f"{source.upper()} erfc edge: {terminal_v:+.2f} ± {fit['velocity_formal_error_kms']:.2f} km/s")
                print(f"Erfc edge ({source}): {terminal_v:+.3f} ± {fit['velocity_formal_error_kms']:.3f} km/s (formal); width={fit['width_kms']:.3f} km/s")
            else:
                print(f"Erfc edge ({source}): {fit['status'].replace('_', ' ')}")
                ax.text(.02, .02 if source == "lab" else .07, f"{source.upper()} erfc: {fit['status'].replace('_', ' ')}",
                        transform=ax.transAxes, fontsize=9)
        fits_by_source = terminal["spectra"]
        if all(fits_by_source.get(source, {}).get("status") == "fitted" for source in ("lab", "observation")):
            difference = fits_by_source["observation"]["velocity_lsr_kms"] - fits_by_source["lab"]["velocity_lsr_kms"]
            terminal["observation_minus_lab_kms"] = difference
            print(f"Erfc edge difference (observation − LAB): {difference:+.3f} km/s")
    elif not args.no_terminal_velocity:
        spectra = {"lab": (result.velocity, result.temperature)}
        if observation is not None:
            spectra["observation"] = (velocity, scaled)
        estimates = {}
        connected = args.terminal_method == "connected"
        threshold_source = "observation" if observation is not None else "lab"
        reference = None
        if connected:
            reference_v, reference_t = spectra[threshold_source]
            reference = estimate_terminal_velocity(reference_v, reference_t, args.vmin, args.vmax,
                                                   args.noise_range, args.terminal_sigma, args.terminal_min_channels, **noise_policy)
        threshold_drawn = False
        for source, (edge_v, edge_t) in spectra.items():
            if args.terminal_source not in ("auto", source):
                continue
            if connected:
                estimate = estimate_connected_terminal_velocity(edge_v, edge_t, args.vmin, args.vmax,
                    args.noise_range, args.terminal_sigma, args.terminal_min_channels,
                    return_width=args.terminal_return_width, threshold_reference=reference,
                    threshold_source=threshold_source, **noise_policy)
            else:
                estimate = estimate_terminal_velocity(edge_v, edge_t, args.vmin, args.vmax,
                                                       args.noise_range, args.terminal_sigma, args.terminal_min_channels, **noise_policy)
            estimate["source"] = source
            estimates[source] = estimate
            colour = "tab:blue" if source == "lab" else "tab:purple"
            if estimate["threshold_k"] is not None and (not connected or not threshold_drawn):
                floor_label = (f"Shared {threshold_source} noise floor ({estimate['noise_level_k']:.2f}, plot units)"
                               if connected else f"{source.upper()} noise floor ({estimate['noise_level_k']:.2f} K)")
                ax.axhline(estimate["noise_level_k"], color=colour, linestyle="--", linewidth=1., alpha=.7,
                           label=floor_label)
                label = (f"Shared {threshold_source} noise floor + {args.terminal_sigma:g}σ ({estimate['threshold_k']:.2f}, plot units)"
                         if connected else f"{source.upper()} noise floor + {args.terminal_sigma:g}σ ({estimate['threshold_k']:.2f} K)")
                ax.axhline(estimate["threshold_k"], color=colour, linestyle=":", alpha=.7,
                           label=label)
                threshold_drawn = True
            if estimate["status"] == "detected":
                terminal_v = estimate["velocity_lsr_kms"]
                ax.axvline(terminal_v, color=colour, linestyle="-.",
                           label=f"{'Main-wing detection edge' if connected else 'Terminal velocity'} ({source}): {terminal_v:+.2f} km/s")
                ax.plot(terminal_v, estimate["threshold_k"], "o", color=colour)
                print(f"Terminal LSR velocity ({source}): {terminal_v:+.3f} km/s; noise scatter={estimate['noise_rms_k']:.4f} K; threshold={estimate['threshold_k']:.4f} K")
            else:
                reason = estimate["status"].replace("_", " ")
                if estimate["status"] in ("edge_limited", "gap_limited"):
                    reason += f"; emission extends to {estimate['last_above_threshold_velocity_kms']:+.2f} km/s"
                ax.text(.02, .02 if source == "lab" else .07, f"Terminal velocity ({source}): {reason}",
                        transform=ax.transAxes, fontsize=9, bbox={"facecolor": "white", "alpha": .8, "edgecolor": "none"})
                print(f"Terminal velocity ({source}): {reason}")
        # Keep the previously selected source's top-level fields for JSON consumers.
        primary = "observation" if "observation" in estimates else "lab"
        terminal = {**estimates[primary], "method": args.terminal_method, "spectra": estimates}
        if all(estimates.get(source, {}).get("status") == "detected" for source in ("lab", "observation")):
            difference = estimates["observation"]["velocity_lsr_kms"] - estimates["lab"]["velocity_lsr_kms"]
            terminal["observation_minus_lab_kms"] = difference
            print(f"Terminal velocity difference (observation − LAB): {difference:+.3f} km/s")
    title = f"LAB: l={result.longitude:.3f}°, b={result.latitude:.3f}° | Beam FWHM={args.beam:g}°"
    observation_id = context.obs_id
    if observation_id is None and args.file is not None and args.file.stem.endswith("_vlsr"):
        observation_id = args.file.stem.removesuffix("_vlsr")
    if observation_id:
        fig.set_figheight(6.4)
    if observation is not None:
        title = (f"Target={args.target_id} | Frequency scan={args.freq_scan} | Beam FWHM={args.beam:g}°"
                 f"\nBest FIT LAB: l={result.longitude:.3f}°, b={result.latitude:.3f}°"
                 f" | Offsets: Δl={result.lon_offset:+.3f}°, Δb={result.lat_offset:+.3f}°"
                 f"\nBaseline={args.observation_baseline} | Fit RMS={result.rms:.5f} | Correlation={result.correlation:.5f}")
    if all(source in terminal.get("spectra", {}) for source in ("lab", "observation")):
        difference = terminal.get("observation_minus_lab_kms")
        title += (f"\nTerminal Velocity (Δv_T): {difference:+.2f} km/s (observation − LAB)" if difference is not None
                  else "\nTerminal Velocity (Δv_T): unavailable (observation − LAB)")
    ax.set(title=title, xlabel="LSR velocity [km/s]", ylabel="Brightness temperature / scaled observation [K]" if observation else "Brightness temperature [K]", xlim=(args.vmin, args.vmax))
    if observation_id:
        header_size = ax.title.get_fontsize()
        fig.suptitle(f"Observation: {observation_id}", fontsize=header_size).set_in_layout(False)
        ax.title.set_fontsize(max(8., header_size-3.))
    ax.grid(alpha=.3)
    ax.legend(loc="upper right", fontsize=9)
    def ranges_text(windows):
        return "; ".join(f"{low:.1f} to {high:.1f}" for low, high in windows) + " km/s"
    window_notes = []
    if observation is not None:
        if args.baseline_window:
            mode = "auto" if args.baseline_auto else "manual"
            window_notes.append(f"Baseline ({mode}): {ranges_text(args.baseline_window)}")
        elif args.observation_baseline in ("linear", "constant"):
            window_notes.append("Baseline: lowest 20% of scoring channels")
        else:
            window_notes.append("Baseline: none")
    if args.no_terminal_velocity:
        window_notes.append("Noise threshold: disabled")
    elif args.terminal_method == "erfc":
        window_notes.append("Noise threshold: not used (erfc)")
    elif args.terminal_method == "connected" and terminal.get("noise_ranges_kms"):
        window_notes.append(f"Noise ({terminal['threshold_source']}, shared): {ranges_text(terminal['noise_ranges_kms'])}")
    else:
        noise_windows = {}
        for source, estimate in terminal.get("spectra", {}).items():
            windows = estimate.get("noise_ranges_kms", [])
            if windows:
                key = tuple(tuple(bounds) for bounds in windows)
                noise_windows.setdefault(key, []).append(source)
        for windows, sources in noise_windows.items():
            window_notes.append(f"Noise ({', '.join(sources)}): {ranges_text(windows)}")
    if noise_metrics is not None:
        if (args.no_terminal_velocity or args.terminal_method == "erfc"
                or "observation" not in terminal.get("spectra", {})) and noise_metrics["noise_ranges_kms"]:
            window_notes.append(f"Observation SNR noise window: {ranges_text(noise_metrics['noise_ranges_kms'])}")
        if noise_metrics["status"] == "measured":
            ratio_label = (f"{noise_metrics['peak_snr']:.1f}" if noise_metrics["peak_snr"] is not None else "out of range")
            window_notes.append(f"Obs peak SNR: {ratio_label} ({noise_metrics['peak_snr_db']:.2f} dB, 10 log)")
            robust = noise_metrics["noise_estimator"] == "median_mad"
            window_notes.append(f"Obs {'median baseline' if robust else 'mean floor'}: {noise_metrics['noise_level_k']:.3g}; "
                                f"{'Robust Noise' if robust else 'RMS'}: {noise_metrics['noise_rms_k']:.3g} (plot units)")
        else:
            window_notes.append(f"Obs SNR/noise: {noise_metrics['status'].replace('_', ' ')}")
    if window_notes:
        ax.text(.015, .98, "\n".join(window_notes), transform=ax.transAxes, va="top", ha="left",
                fontsize=8, bbox={"boxstyle": "round,pad=0.4", "facecolor": "white", "edgecolor": "0.7", "alpha": .9})
    if observation_id:
        header_margin = (header_size+6.)/(72.*fig.get_figheight())
        fig.tight_layout(rect=(0, 0, 1, 1-header_margin))
    else:
        fig.tight_layout()
    if not args.display_only:
        prefix = args.output.expanduser().with_suffix("") if args.output else Path(args.output_dir or args.dir).expanduser() / (f"{context.output_name}-lab-profile" if observation is None else f"{args.file.stem}_target{args.target_id}_scan{args.freq_scan}_lab_fit")
        prefix.parent.mkdir(parents=True, exist_ok=True)
        def destination(suffix):
            return Path(f"{prefix}{suffix}")
        np.savetxt(destination(".csv"), np.column_stack([result.velocity, result.temperature]), delimiter=",", header="velocity_lsr_kms,brightness_K", comments="")
        fig.savefig(destination(".png"), dpi=180)
        metadata = {**context.metadata(), "survey": "LAB Galactic CAR cube", "lab_fits": str(args.lab_fits.expanduser().resolve()),
                    "beam_fwhm_deg": args.beam, "beam_method": "Gaussian in Galactic coordinate degrees; three sigma aperture",
                    "best_longitude_deg": result.longitude, "best_latitude_deg": result.latitude,
                    "longitude_offset_deg": result.lon_offset, "latitude_offset_deg": result.lat_offset,
                    "target_id": args.target_id, "freq_scan": args.freq_scan,
                    "observation_file": str(args.file.expanduser().resolve()) if args.file else None,
                    "rms": result.rms if np.isfinite(result.rms) else None,
                    "correlation": result.correlation if np.isfinite(result.correlation) else None,
                    "terminal_velocity": terminal}
        if observation is not None:
            metadata.update(observation_noise=noise_metrics,
                            observation_baseline=args.observation_baseline,
                            baseline_windows_kms=args.baseline_window,
                            baseline_window_selection=("automatic_outer_20_percent_per_side" if args.baseline_auto and args.baseline_window
                                                       else "explicit" if args.baseline_window else "method_default"),
                            baseline_method=(f"{args.observation_baseline} fit to {'automatically selected outer' if args.baseline_auto else 'explicit line-free'} windows with symmetric 3-sigma clipping (IQR/1.349, standard deviation fallback; up to five iterations)" if args.baseline_window else {"linear": "linear fit to lowest 20% of powers on shared fitting channels (HI4PI method)",
                                             "constant": "median of lowest 20% of powers on shared fitting channels (HI4PI method)",
                                             "none": "no subtraction"}[args.observation_baseline]),
                            channel_matching="drop leading LAB channel if odd; average pairs; interpolate for scoring", fit_velocity_range_kms=[args.fit_vmin, args.fit_vmax], observation_peak_scale=float(np.nanmax(result.temperature) / np.max(corrected)))
            reference = np.interp(velocity, result.velocity, result.temperature, left=np.nan, right=np.nan)
            np.savetxt(destination("_comparison.csv"), np.column_stack([velocity, baseline, corrected, scaled, reference]), delimiter=",", header="velocity_lsr_kms,baseline,baseline_subtracted_power,observation_scaled_to_lab_K,lab_K", comments="")
        destination(".json").write_text(json.dumps(metadata, indent=2, allow_nan=False) + "\n")
        print(f"Saved: {destination('.csv')}, {destination('.png')}, {destination('.json')}")
    if not args.no_show:
        plt.show()
    plt.close(fig)


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.lon is not None or args.lat is not None:
            if args.lon is None or args.lat is None or args.galactic is not None:
                raise ValueError("Provide both --lon and --lat, or --galactic L B")
            args.galactic = [args.lon, args.lat]
        context = resolve_profile_context(args)
        # Manual windows take precedence. Modes that do not fit line-free
        # channels (or have no observation) do not need automatic selection.
        if args.baseline_window or args.file is None or args.observation_baseline not in ("linear", "constant"):
            args.baseline_auto = False
        if args.baseline_auto:
            if not args.vmin < 0 < args.vmax:
                raise ValueError("--baseline-auto requires --vmin < 0 < --vmax")
        if args.baseline_window:
            if args.file is None or args.observation_baseline not in ("linear", "constant"):
                raise ValueError("--baseline-window requires --file and a linear or constant observation baseline")
            for low, high in args.baseline_window:
                if not np.all(np.isfinite([low, high])) or low >= high:
                    raise ValueError("Baseline windows must be finite with minimum < maximum")
        if not np.isfinite(args.beam) or args.beam <= 0:
            raise ValueError("Beam must be finite and positive")
        if args.lv:
            if args.sweep or args.file is not None or args.target_id is not None or args.freq_scan is not None:
                raise ValueError("--lv predicts survey profiles; do not combine with --sweep or observation CSV selection")
            if not np.isfinite(args.lab_native_beam) or args.lab_native_beam < 0 or args.beam <= args.lab_native_beam:
                raise ValueError("Final --beam must exceed the non-negative --lab-native-beam for --lv")
            if not np.isfinite(args.lv_asinh_width) or args.lv_asinh_width <= 0:
                raise ValueError("L–V asinh width must be finite and positive")
            limits = [v for v in (args.lv_brightness_min, args.lv_brightness_max) if v is not None]
            if not np.all(np.isfinite(limits)):
                raise ValueError("L–V brightness limits must be finite")
            if args.lv_brightness_max is not None and args.lv_brightness_min is not None and args.lv_brightness_max <= args.lv_brightness_min:
                raise ValueError("Upper L–V brightness limit must exceed the lower limit")
            if args.lv_contours is not None and (not np.all(np.isfinite(args.lv_contours)) or min(args.lv_contours) <= 0 or np.any(np.diff(args.lv_contours) <= 0)):
                raise ValueError("L–V contour levels must be finite, positive and strictly increasing")
        if not np.all(np.isfinite([args.vmin, args.vmax, args.fit_vmin, args.fit_vmax])) or args.vmin >= args.vmax or args.fit_vmin >= args.fit_vmax:
            raise ValueError("Velocity limits must be finite with minimum < maximum")
        if args.file is None and (args.target_id is not None or args.freq_scan is not None or args.sweep):
            raise ValueError("--file, --target-id and --freq-scan are required for observation comparisons and sweeps")
        if args.terminal_method == "erfc" and not args.no_terminal_velocity:
            low, high = args.terminal_fit_range or [max(0., args.vmin), args.vmax]
            if not np.all(np.isfinite([low, high])) or low < 0 or low >= high:
                raise ValueError("Erfc fit range must be finite with 0 <= minimum < maximum")
            if low < args.vmin or high > args.vmax:
                raise ValueError("Erfc fit range must be inside the displayed velocity range (--vmin/--vmax)")
        if args.file is not None and (args.target_id is None or args.freq_scan is None):
            raise ValueError("--target-id and --freq-scan are required with --file")
        if args.display_only and (args.no_show or args.output is not None):
            raise ValueError("--display-only cannot be combined with --no-show or --output")
        if args.terminal_source == "observation" and args.file is None and not args.no_terminal_velocity:
            raise ValueError("--terminal-source observation requires --file, --target-id and --freq-scan")
        if not np.isfinite(args.noise_window_frac) or not 0 < args.noise_window_frac < 1 or args.noise_smooth_window < 1:
            raise ValueError("Noise window fraction must be between zero and one; smoothing must be positive")
        if not np.isfinite(args.terminal_sigma) or args.terminal_sigma <= 0 or args.terminal_min_channels < 1:
            raise ValueError("Terminal sigma must be finite and positive; minimum channels must be positive")
        if not np.isfinite(args.terminal_return_width) or args.terminal_return_width <= 0:
            raise ValueError("Terminal return width must be finite and positive")
        for low, high in args.noise_range or []:
            if not np.all(np.isfinite([low, high])) or low >= high:
                raise ValueError("Noise windows must be finite with minimum < maximum")
        lon_offsets = range_values(args.lon_range) if args.sweep or args.lv else [0.]
        if args.lv:
            if lon_offsets[-1] - lon_offsets[0] > 360 + 1e-9:
                raise ValueError("Predicted L–V longitude span must not exceed 360 degrees")
            if len(lon_offsets) > 1 and np.isclose(lon_offsets[-1] - lon_offsets[0], 360, rtol=0, atol=1e-9):
                lon_offsets = lon_offsets[:-1]  # Do not repeat the circular endpoint.
        lat_offsets = range_values(args.lat_range) if args.sweep else [0.]
        if len(lon_offsets) * len(lat_offsets) > 100000:
            raise ValueError("Sweep exceeds 100000 candidates")
    except ValueError as exc:
        parser.error(str(exc))
    except (OSError, KeyError) as exc:
        parser.exit(1, f"LAB metadata lookup failed: {exc}\n")
    try:
        if args.lv:
            plot_predicted_lv(args, context, lon_offsets)
            return 0
        observation = None
        if args.file is not None:
            velocity, power = load_observation(args.file, args.target_id, args.freq_scan)
            if args.baseline_auto:
                args.baseline_window = select_baseline_windows(velocity, power, args.vmin, args.vmax)
                for low, high in args.baseline_window:
                    print(f"Automatic baseline window: {low:.3f} to {high:.3f} km/s")
        longitude, latitude = context.galactic.l.deg, context.galactic.b.deg
        best, rows = None, []
        with LABGaussianExtractor(args.lab_fits) as extractor:
            for dl in lon_offsets:
                for db in lat_offsets:
                    lon, lat = (longitude + dl) % 360, latitude + db
                    if not -90 <= lat <= 90:
                        continue
                    ref_v, ref_t = extractor.extract(lon, lat, args.beam)
                    rms, correlation = np.nan, np.nan
                    candidate_observation = None
                    if args.file is not None:
                        ref_v, ref_t = match_channels(ref_v, ref_t)
                        order = np.argsort(ref_v)
                        ref_v, ref_t = ref_v[order], ref_t[order]
                        reference = np.interp(velocity, ref_v, ref_t, left=np.nan, right=np.nan)
                        fit_mask = np.isfinite(reference) & (velocity >= args.fit_vmin) & (velocity <= args.fit_vmax)
                        if fit_mask.sum() >= 10:
                            corrected, baseline = subtract_baseline(velocity, power, args.observation_baseline, fit_mask,
                                                                    baseline_windows=args.baseline_window)
                            candidate_observation = velocity, corrected, baseline
                            rms, correlation = compare_spectra(velocity, corrected, ref_v, ref_t, args.fit_vmin, args.fit_vmax)
                        else:
                            rms = np.inf
                    result = FitResult(float(lon), float(lat), float(dl), float(db), rms, correlation, ref_v, ref_t)
                    rows.append([lon, lat, dl, db, rms, correlation])
                    if args.file is not None:
                        print(f"l={lon:.4f} b={lat:.4f} Δl={dl:+.4f} Δb={db:+.4f} RMS={rms:.6f} corr={correlation:.6f}")
                    if args.file is None or (np.isfinite(rms) and (best is None or rms < best.rms)):
                        best = result
                        observation = candidate_observation
        if best is None:
            raise ValueError("No valid LAB comparison found; check velocity overlap, fitting limits and signal")
        if not np.any(np.isfinite(best.temperature)):
            raise ValueError("LAB profile contains no finite temperatures")
        plot_result(best, args, context, observation)
        if args.sweep:
            print(f"Best match: l={best.longitude:.4f}°, b={best.latitude:.4f}°; offsets Δl={best.lon_offset:+.4f}°, Δb={best.lat_offset:+.4f}°")
            if not args.display_only:
                path = args.output.expanduser().with_suffix("") if args.output else Path(args.output_dir or args.dir).expanduser() / f"{args.file.stem}_target{args.target_id}_scan{args.freq_scan}_lab_fit"
                path = Path(f"{path}_sweep.csv")
                np.savetxt(path, rows, delimiter=",", header="longitude_deg,latitude_deg,longitude_offset_deg,latitude_offset_deg,rms,correlation", comments="")
                print(f"Saved: {path}")
    except (OSError, ValueError, RuntimeError, KeyError, u.UnitsError) as exc:
        parser.exit(1, f"LAB profiling failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
