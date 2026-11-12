import csv
import logging
from typing import Iterable

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.gridspec import GridSpec
from matplotlib.ticker import AutoMinorLocator, MaxNLocator
from obs.lsr import HI_REST_FREQUENCY
from util.convert import select_baseline_windows
from sdp.spectral_baseline import subtract_baseline as subtract_spectrum_baseline
from obs.spectral_annotations import annotate_spectral_metrics, place_spectral_metrics
from util.lv_plot import reference_lv_colormap, interpolate_lv_contour_column
from sdp.channel_mask import (
    ChannelFlag,
    channels_with_flag,
    contiguous_regions,
    empty_channel_flags,
    masked_values,
    reconstructed_total_power,
    valid_channels,
)

logger = logging.getLogger(__name__)

mpl.rcParams["figure.raise_window"] = False

FIG_SIZE = (14, 8)
SCAN_COLOURS = (
    "tab:blue",
    "tab:orange",
    "tab:green",
    "tab:red",
    "tab:purple",
    "tab:brown",
    "tab:pink",
    "tab:gray",
    "tab:cyan",
)


def _scan_colours(count: int) -> list[str]:
    """Return readable categorical colours without yellow or olive tones."""
    return [SCAN_COLOURS[idx % len(SCAN_COLOURS)] for idx in range(count)]


def mark_hi_rest_frequency(axes):
    """Mark the fixed H I rest frequency when it falls inside the MHz view."""
    frequency = HI_REST_FREQUENCY.to_value("MHz")
    low, high = sorted(axes.get_xlim())
    if low <= frequency <= high:
        axes.axvline(frequency, color="black", linestyle="--", linewidth=1,
                     label=f"HI rest ({frequency:.3f} MHz)")


def save_velocity_figure(figure, axes, output_path):
    """Keep velocity data axes full-sized and extend the PNG for its legend."""
    handles, labels = axes.get_legend_handles_labels()
    legend = axes.legend(handles, labels, loc="upper left", bbox_to_anchor=(1.015, 1),
                         borderaxespad=0, ncol=max(1, int(np.ceil(len(handles) / 16))),
                         fontsize=8.5, handlelength=3, columnspacing=1.2)
    legend.set_in_layout(False)
    figure.tight_layout()
    metrics = place_spectral_metrics(figure, axes, legend)
    figure.savefig(output_path, dpi=150, bbox_inches="tight", bbox_extra_artists=(legend, *metrics))


def spectral_channel_edges(coordinates):
    """Cell boundaries for finite, increasing native spectral coordinates."""
    values = np.asarray(coordinates, dtype=float)
    if values.ndim != 1 or len(values) < 2 or not np.all(np.isfinite(values)) or np.any(np.diff(values) <= 0):
        raise ValueError("Spectral coordinates need at least two finite, increasing channels")
    return np.r_[values[0] - (values[1]-values[0])/2,
                 (values[:-1]+values[1:])/2, values[-1] + (values[-1]-values[-2])/2]


def velocity_limits(velocity, vmin=None, vmax=None):
    """Resolve blank limits from all recorded channels, including flagged edges."""
    edges = spectral_channel_edges(np.unique(np.asarray(velocity, dtype=float)))
    low = edges[0] if vmin is None else float(vmin)
    high = edges[-1] if vmax is None else float(vmax)
    if not np.all(np.isfinite([low, high])) or low >= high:
        raise ValueError("Velocity limits must be finite with minimum < maximum")
    return low, high


def plot_channel_flags(axis, coordinates, power, flags, *, shaded_regions=None):
    """Annotate native channels in either frequency or velocity coordinates."""
    if flags is None:
        return
    order = np.argsort(coordinates)
    x = np.asarray(coordinates)[order]
    y = np.ma.masked_invalid(power)[order].filled(np.nan)
    flags = np.asarray(flags)[order]
    edges = spectral_channel_edges(x)
    labels = set(axis.get_legend_handles_labels()[1])
    for start, end in contiguous_regions(channels_with_flag(flags, ChannelFlag.BANDPASS_EXCLUDED)):
        region = (round(float(edges[start]), 9), round(float(edges[end]), 9))
        if shaded_regions is not None:
            if region in shaded_regions:
                continue
            shaded_regions.add(region)
        axis.axvspan(edges[start], edges[end], color="gray", alpha=.12,
                     label="Bandpass Excluded" if "Bandpass Excluded" not in labels else "_nolegend_")
        labels.add("Bandpass Excluded")
    usable = np.flatnonzero(valid_channels(y, flags))
    rfi = np.flatnonzero(channels_with_flag(flags, ChannelFlag.RFI_DETECTED))
    if len(usable) and len(rfi):
        axis.plot(x[rfi], np.interp(x[rfi], x[usable], y[usable]), color="tab:red", marker="x", linestyle="none",
                  label="RFI Flagged" if "RFI Flagged" not in labels else "_nolegend_")


def save_frequency_record(record, output_path, observation, baseline, *, fmin=None, fmax=None):
    """Plot one corrected native spectrum against its observed frequency bins."""
    frequency = np.asarray(record["frequency_mhz"])
    raw = np.ma.masked_invalid(record.get("raw_power", record["power"]))
    power = raw
    if frequency.ndim != 1 or frequency.shape != power.shape or not np.all(np.isfinite(frequency)):
        raise ValueError("Frequency channels must be finite and match the power spectrum")
    low = float(frequency.min()) if fmin is None else fmin
    high = float(frequency.max()) if fmax is None else fmax
    if not np.all(np.isfinite([low, high])) or low <= 0 or low >= high:
        raise ValueError("Frequency limits must be positive and finite with minimum < maximum")
    fit_mask = (frequency >= low) & (frequency <= high)
    if not np.any(fit_mask & ~np.ma.getmaskarray(raw)):
        raise ValueError("No usable frequency channels in the selected range")
    if baseline != "none":
        midpoint = (low + high) / 2
        try:
            windows = select_baseline_windows(frequency - midpoint, raw, low - midpoint, high - midpoint)
        except ValueError as exc:
            raise ValueError("Frequency baseline needs at least five outer channels in each half of the selected MHz range; widen the frequency limits") from exc
        fit_mask = np.zeros(len(frequency), dtype=bool)
        for left, right in windows:
            fit_mask |= (frequency - midpoint >= left) & (frequency - midpoint <= right)
    power, correction = subtract_spectrum_baseline(frequency, raw, baseline, fit_mask,
                                          fit_line_free=baseline != "none")
    order = np.argsort(frequency)
    figure, axes = plt.subplots(figsize=(14, 6))
    try:
        axes.plot(frequency[order], power[order], color=SCAN_COLOURS[0],
                  label=f"Tgt {record['target_id']}, Freq {record['freq_scan']}")
        plot_channel_flags(axes, frequency, power, record.get("channel_flags"))
        axes.set(title=f"Observation {observation} — Frequency spectrum (baseline: {baseline})",
                 xlabel="Frequency (MHz)", ylabel="Calibrated power [a.u.]" if baseline == "none"
                 else "Baseline-subtracted calibrated power [a.u.]", xlim=(low, high))
        axes.ticklabel_format(axis="x", style="plain", useOffset=False)
        mark_hi_rest_frequency(axes)
        axes.grid(alpha=.25)
        annotate_spectral_metrics(axes, frequency, power, low, high,
                                  label=f"Tgt {record['target_id']}, Freq {record['freq_scan']}", unit="MHz",
                                  flags=record.get("channel_flags"), velocity=record.get("velocity"),
                                  qa_settings=record.get("qa_settings"))
        save_velocity_figure(figure, axes, output_path)
    finally:
        plt.close(figure)
    return {**record, "raw_power": raw, "power": power, "frequency_mhz": frequency,
            "frequency_limits": (low, high), "frequency_correction": correction}


def save_frequency_overlay(records, output_path, observation, baseline):
    """Overlay the exact corrected spectra returned by individual Frequency rendering."""
    if not records:
        raise ValueError("No individual Frequency spectra are available with these settings")
    figure, axes = plt.subplots(figsize=(14, 6))
    try:
        shaded_regions = set()
        for index, record in enumerate(records):
            frequency, power = record["frequency_mhz"], record["power"]
            order = np.argsort(frequency)
            axes.plot(frequency[order], power[order], color=SCAN_COLOURS[index % len(SCAN_COLOURS)],
                      label=f"Tgt {record['target_id']}, Freq {record['freq_scan']}", linewidth=1.5)
            plot_channel_flags(axes, frequency, power, record.get("channel_flags"), shaded_regions=shaded_regions)
        low = min(record["frequency_limits"][0] for record in records)
        high = max(record["frequency_limits"][1] for record in records)
        axes.set(title=f"Observation {observation} — All SKY Frequency spectra (baseline: {baseline})",
                 xlabel="Frequency (MHz)", ylabel="Calibrated power [a.u.]" if baseline == "none"
                 else "Baseline-subtracted calibrated power [a.u.]", xlim=(low, high))
        axes.ticklabel_format(axis="x", style="plain", useOffset=False)
        mark_hi_rest_frequency(axes)
        axes.grid(alpha=.25)
        save_velocity_figure(figure, axes, output_path)
    finally:
        plt.close(figure)


class ObsDisplay:
    """Display aggregated observation scans across the full frequency span."""

    def __init__(self, obs_id: str):
        """Initialise the observation display.

        Parameters:
            obs_id: Observation identifier used in the figure title.
        """
        self.obs_id = obs_id
        self.scans = []
        self.fig = None
        self.ax_freq = None
        self.ax_vel = None
        self.freq_min_mhz = None
        self.freq_max_mhz = None
        self.lsr_spectra = []
        self.lsr_baseline_method = None

    def __str__(self):
        return f"ObsDisplay(obs_id={self.obs_id})"

    def _create_figure(self):
        """Create the matplotlib figure and axes."""
        self.fig = plt.figure(num=f"Observation {self.obs_id}", figsize=FIG_SIZE)
        gs = GridSpec(2, 1, height_ratios=[1, 1], hspace=0.28, top=0.92, bottom=0.10, left=0.08, right=0.95)
        self.ax_freq = self.fig.add_subplot(gs[0])
        self.ax_vel = self.fig.add_subplot(gs[1])

    def _prepare_figure(self):
        """Create or clear the figure prior to plotting."""
        if self.fig is None:
            self._create_figure()
        else:
            self.ax_freq.cla()
            self.ax_vel.cla()

    def set_scan(self, scans: Iterable, obs=None):
        """Set the aggregated scans to display.

        Parameters:
            scans: Iterable of aggregated SKY Scan instances to plot.
            obs: Optional Observation model used to derive the full frequency span.
        """
        self.lsr_spectra = []
        if scans is None:
            self.scans = []
            return

        self.scans = sorted(
            [scan for scan in scans if getattr(scan, "scan_model", None) is not None],
            key=lambda scan: (scan.scan_model.center_freq, scan.scan_model.freq_scan),
        )

        self.freq_min_mhz = None
        self.freq_max_mhz = None
        if obs is not None and getattr(obs, "target_scans", None) is not None:
            freq_min_vals = [ts.freq_min / 1e6 for ts in obs.target_scans if getattr(ts, "freq_min", None) is not None]
            freq_max_vals = [ts.freq_max / 1e6 for ts in obs.target_scans if getattr(ts, "freq_max", None) is not None]
            if freq_min_vals:
                self.freq_min_mhz = min(freq_min_vals)
            if freq_max_vals:
                self.freq_max_mhz = max(freq_max_vals)

    def _plot_frequency_panel(self):
        """Plot all aggregated scans across the full frequency range."""
        self.ax_freq.set_title("Aggregated Sky Scans")
        self.ax_freq.set_xlabel("Frequency (MHz)")
        self.ax_freq.ticklabel_format(axis="x", style="plain", useOffset=False)
        self.ax_freq.set_ylabel("Power Spectrum [a.u.]")
        self.ax_freq.grid(True, alpha=0.25)

        if not self.scans:
            self.ax_freq.text(0.5, 0.5, "No aggregated scans available", ha="center", va="center", transform=self.ax_freq.transAxes)
            return

        freq_spans = []
        colours = _scan_colours(len(self.scans))
        shaded_regions = set()
        bandpass_label_added = False
        rfi_label_added = False

        for idx, scan in enumerate(self.scans):
            start_mhz = (scan.scan_model.center_freq - scan.scan_model.sample_rate / 2.0) / 1e6
            end_mhz = (scan.scan_model.center_freq + scan.scan_model.sample_rate / 2.0) / 1e6
            freq_axis = (scan.scan_model.center_freq + np.fft.fftshift(
                np.fft.fftfreq(scan.scan_model.spectral_resolution, d=1 / scan.scan_model.sample_rate)
            )) / 1e6
            freq_spans.extend([start_mhz, end_mhz])

            y = scan.mpr if scan.mpr is not None else np.zeros(scan.scan_model.spectral_resolution)
            mpr_flags = getattr(scan, "mpr_flags", None)
            if not isinstance(mpr_flags, np.ndarray) or mpr_flags.shape != y.shape:
                mpr_flags = empty_channel_flags(y.shape)
            usable = valid_channels(y, mpr_flags)
            label = f"Tgt {scan.scan_model.tgt_idx}, Freq {scan.scan_model.freq_scan}"
            self.ax_freq.plot(freq_axis, masked_values(y, mpr_flags), color=colours[idx], linewidth=1.5, label=label)

            rfi = channels_with_flag(mpr_flags, ChannelFlag.RFI_DETECTED)
            rfi &= np.isfinite(y)
            rfi_indices = np.flatnonzero(rfi)
            usable_indices = np.flatnonzero(usable)
            if rfi_indices.size > 0 and usable_indices.size > 0:
                marker_values = np.interp(rfi_indices, usable_indices, y[usable_indices])
                self.ax_freq.plot(
                    freq_axis[rfi_indices],
                    marker_values,
                    color="tab:red",
                    marker="x",
                    linestyle="none",
                    label="RFI Flagged" if not rfi_label_added else "_nolegend_",
                )
                rfi_label_added = True

            bandpass = channels_with_flag(mpr_flags, ChannelFlag.BANDPASS_EXCLUDED)
            for start, end in contiguous_regions(bandpass):
                region_start = start_mhz + (end_mhz - start_mhz) * start / y.size
                region_end = start_mhz + (end_mhz - start_mhz) * end / y.size
                region_key = (round(region_start, 9), round(region_end, 9))
                if region_key in shaded_regions:
                    continue
                self.ax_freq.axvspan(
                    region_start,
                    region_end,
                    color="gray",
                    alpha=0.12,
                    label="Bandpass Excluded" if not bandpass_label_added else "_nolegend_",
                )
                shaded_regions.add(region_key)
                bandpass_label_added = True

        if freq_spans:
            self.ax_freq.set_xlim(min(freq_spans), max(freq_spans))

        mark_hi_rest_frequency(self.ax_freq)

        handles, labels = self.ax_freq.get_legend_handles_labels()
        self.ax_freq.legend(handles, labels, loc="upper left", bbox_to_anchor=(1.015, 1),
                            borderaxespad=0, ncol=max(1, int(np.ceil(len(handles) / 17))),
                            fontsize=8, handlelength=2.5, columnspacing=1.2)

    def _plot_velocity_panel(self):
        """Use the lower half of the display for integrated total power per synthesized scan row."""
        self.ax_vel.set_title("Integrated Total Power")
        self.ax_vel.set_xlabel("Integrated Scans")
        self.ax_vel.set_ylabel("Power [a.u.]")
        self.ax_vel.xaxis.set_major_locator(MaxNLocator(nbins="auto", integer=True))
        self.ax_vel.xaxis.set_minor_locator(AutoMinorLocator())
        self.ax_vel.grid(True, which="major", alpha=0.25)
        self.ax_vel.grid(True, which="minor", axis="x", alpha=0.12)

        if not self.scans:
            self.ax_vel.text(
                0.5,
                0.5,
                "No aggregated scans available",
                ha="center",
                va="center",
                transform=self.ax_vel.transAxes,
            )
            return

        colours = _scan_colours(len(self.scans))
        max_secs = 0

        for idx, scan in enumerate(self.scans):
            if scan.cal is None or scan.get_loaded_seconds() <= 0:
                continue

            loaded_seconds = scan.get_loaded_seconds()
            cal_values = scan.cal[:loaded_seconds, :]
            cal_flags = getattr(scan, "cal_flags", None)
            if cal_flags is None:
                cal_flags = empty_channel_flags(scan.cal.shape)
            tpw_sum, _, _ = reconstructed_total_power(
                cal_values,
                cal_flags[:loaded_seconds, :],
            )
            if tpw_sum.size == 0:
                continue

            time_axis = np.arange(tpw_sum.size) + 1
            label = f"Tgt {scan.scan_model.tgt_idx}, Freq {scan.scan_model.freq_scan}"
            # Pair repeating colours with line styles so 31 scans remain distinct.
            styles = ("-", "-.", ":", (0, (5, 1, 1, 1, 1, 1)))
            self.ax_vel.plot(time_axis, tpw_sum, color=colours[idx],
                             linestyle=styles[(idx // len(SCAN_COLOURS)) % len(styles)],
                             linewidth=1.5, label=label)
            finite_tpw = tpw_sum[np.isfinite(tpw_sum)]
            if finite_tpw.size > 0:
                mean_tpw = float(np.mean(finite_tpw))
                self.ax_vel.axhline(mean_tpw, color=colours[idx], linestyle="--", linewidth=1.0, alpha=0.45)
            max_secs = max(max_secs, time_axis[-1])

        if max_secs > 0:
            self.ax_vel.set_xlim(1, max_secs)

        handles, labels = self.ax_vel.get_legend_handles_labels()
        if handles:
            self.ax_vel.legend(handles, labels, loc="upper left", bbox_to_anchor=(1.015, 1),
                               borderaxespad=0, ncol=max(1, int(np.ceil(len(handles) / 16))),
                               fontsize=8.5, handlelength=3, columnspacing=1.2)

    def display(self):
        """Render the observation display."""
        self._prepare_figure()
        self.fig.suptitle(f"Observation {self.obs_id}", fontsize=12)
        self._plot_frequency_panel()
        self._plot_velocity_panel()
        self.fig.canvas.draw_idle()
        plt.show(block=False)

    def save_frequency_spectrum(self, output_path: str) -> str:
        """Save the frequency spectrum alone, with its legend outside the axes."""
        figure, axes = plt.subplots(figsize=(14, 6))
        previous = self.ax_freq
        try:
            self.ax_freq = axes
            self._plot_frequency_panel()
            figure.suptitle(f"Observation {self.obs_id}", fontsize=12)
            legend = axes.get_legend()
            if legend is not None:
                legend.set_in_layout(False)
            figure.tight_layout()
            metrics = place_spectral_metrics(figure, axes, legend)
            figure.savefig(output_path, dpi=150, bbox_inches="tight",
                           bbox_extra_artists=((legend,) if legend is not None else ()) + tuple(metrics))
        finally:
            self.ax_freq = previous
            plt.close(figure)
        return output_path

    def save_integrated_total_power(self, output_path: str) -> str:
        """Save the Integrated Total Power panel as a standalone PNG."""
        output_fig, output_axes = plt.subplots(figsize=(14, 6))
        display_axes = self.ax_vel

        try:
            self.ax_vel = output_axes
            self._plot_velocity_panel()
            output_axes.set_title(f"Observation {self.obs_id} — Integrated Total Power")
            legend = output_axes.get_legend()
            if legend is not None:
                # Keep the data axes full size; extend the exported image for
                # the legend instead of letting tight_layout squeeze the plot.
                legend.set_in_layout(False)
            output_fig.tight_layout()
            output_fig.savefig(output_path, dpi=150, bbox_inches="tight",
                               bbox_extra_artists=(legend,) if legend is not None else ())
        finally:
            self.ax_vel = display_axes
            plt.close(output_fig)

        return output_path

    def display_lsr(self, obs, output_path: str, blacklist=(), csv_path: str | None = None,
                    observation_baseline="linear", baseline_vmin=None, baseline_vmax=None,
                    baseline_windows=None, *, vmin=-100., vmax=100., baseline_auto=True, show_metrics=True,
                    metric_settings=None):
        """Show and save calibrated aggregates in LSRK, one curve per frequency scan.

        Fit each target/frequency scan separately without amplitude scaling.
        Auto windows select the outer 20% on each side within vmin/vmax.
        Explicit windows override selection, independently of those limits.
        Both window modes fit all selected channels with symmetric clipping.
        Disable baseline_auto for the legacy lower-envelope fit within vmin/vmax.
        baseline_vmin/baseline_vmax are compatibility aliases for vmin/vmax.
        CSV preserves calibrated_power and adds baseline/baseline_subtracted_power.
        Masked powers remain blank. Input MPR arrays are never changed. A scan
        with insufficient baseline samples is logged and skipped, not shown raw.
        """
        from obs.lsr import lsr_velocity, scan_observing_context

        self.lsr_spectra = []
        self.lsr_baseline_method = observation_baseline
        if observation_baseline not in ("none", "constant", "linear", "flatten"):
            raise ValueError("Observation baseline must be none, constant, linear or flatten")
        if baseline_vmin is not None:
            vmin = baseline_vmin
        if baseline_vmax is not None:
            vmax = baseline_vmax
        if any(value is not None and not np.isfinite(value) for value in (vmin, vmax)) or (vmin is not None and vmax is not None and vmin >= vmax):
            raise ValueError("Baseline velocity limits must be finite with minimum < maximum")
        use_auto = baseline_auto and not baseline_windows and observation_baseline != "none"
        if use_auto and ((vmin is not None and vmin >= 0) or (vmax is not None and vmax <= 0)):
            raise ValueError("--baseline-auto requires --vmin < 0 < --vmax; otherwise provide --baseline-window")
        for low, high in baseline_windows or []:
            if not np.all(np.isfinite([low, high])) or low >= high:
                raise ValueError("Baseline windows must be finite with minimum < maximum")
        figure, axes = plt.subplots(num=f"LSRK Observation {self.obs_id}", figsize=(14, 6))
        plotted = 0
        csv_rows = []
        for scan, colour in zip(self.scans, _scan_colours(len(self.scans))):
            model = scan.scan_model
            if scan.mpr is None:
                continue
            try:
                direction, location, observed_at = scan_observing_context(obs, scan, blacklist)
                frequencies = model.center_freq + np.fft.fftshift(
                    np.fft.fftfreq(model.spectral_resolution, d=1 / model.sample_rate)
                )
                velocity = lsr_velocity(frequencies, direction, location, observed_at)
            except (ValueError, TypeError, AttributeError) as exc:
                logger.warning("Skipping LSRK plot for %s: %s", model.scan_id, exc)
                continue
            flags = getattr(scan, "mpr_flags", None)
            if not isinstance(flags, np.ndarray) or flags.shape != scan.mpr.shape:
                flags = empty_channel_flags(scan.mpr.shape)
            order = np.argsort(velocity)
            sorted_velocity = velocity[order]
            sorted_power = masked_values(scan.mpr, flags)[order]
            try:
                low, high = velocity_limits(sorted_velocity, vmin, vmax)
                selected_windows = baseline_windows
                if use_auto:
                    selected_windows = select_baseline_windows(sorted_velocity, sorted_power, low, high)
                fit_mask = (sorted_velocity >= low) & (sorted_velocity <= high)
                if selected_windows:
                    fit_mask = np.zeros(len(sorted_velocity), dtype=bool)
                    for left, right in selected_windows:
                        fit_mask |= (sorted_velocity >= left) & (sorted_velocity <= right)
                corrected_power, baseline = subtract_spectrum_baseline(
                    sorted_velocity, sorted_power, observation_baseline, fit_mask,
                    fit_line_free=bool(selected_windows))
            except ValueError as exc:
                logger.warning("Skipping LSRK baseline for target %s, frequency %s: %s",
                               model.tgt_idx, model.freq_scan, exc)
                continue
            axes.plot(
                sorted_velocity, corrected_power, color=colour,
                label=f"Tgt {model.tgt_idx}, Freq {model.freq_scan}",
            )
            plot_channel_flags(axes, sorted_velocity, corrected_power, flags[order])
            if show_metrics:
                annotate_spectral_metrics(axes, sorted_velocity, corrected_power, low, high,
                                          label=f"Tgt {model.tgt_idx}, Freq {model.freq_scan}", flags=flags[order],
                                          qa_settings=metric_settings)
            self.lsr_spectra.append({
                "target_id": model.tgt_idx, "freq_scan": model.freq_scan,
                "center_freq": model.center_freq, "direction": direction,
                "velocity": sorted_velocity.copy(), "power": corrected_power.copy(),
                "raw_power": sorted_power.copy(),
                "channel_flags": flags[order].copy(),
                "qa_settings": metric_settings,
                "baseline_windows": selected_windows, "baseline": baseline.copy(),
            })
            if csv_path is not None:
                def csv_value(value):
                    return "" if np.ma.is_masked(value) or not np.isfinite(value) else float(value)
                csv_rows.extend(
                    (model.tgt_idx, model.freq_scan, float(v), csv_value(power), csv_value(base), csv_value(corrected))
                    for v, power, base, corrected in zip(sorted_velocity, sorted_power, baseline, corrected_power)
                )
            plotted += 1
        if not plotted:
            plt.close(figure)
            logger.warning("No LSRK plot produced: no SKY scans with sufficient observing metadata and baseline samples")
            return None
        axes.axvline(0, color="black", linestyle="--", linewidth=1, label="Zero LSRK velocity")
        axes.set_title(f"Observation {self.obs_id} — Aggregated Sky Scans (baseline: {observation_baseline})")
        axes.set_xlim(*velocity_limits(np.concatenate([record["velocity"] for record in self.lsr_spectra]), vmin, vmax))
        axes.set_xlabel("LSRK velocity [km/s] (radio convention; H I rest 1420.405751768 MHz)")
        axes.set_ylabel("Calibrated Power Spectrum [a.u.]" if observation_baseline == "none"
                        else "Baseline-subtracted calibrated power [a.u.]")
        axes.grid(True, alpha=0.25)
        save_velocity_figure(figure, axes, output_path)
        if csv_path is not None:
            with open(csv_path, "w", newline="", encoding="utf-8") as csv_file:
                writer = csv.writer(csv_file)
                writer.writerow(["target_id", "freq_scan", "velocity_km_s", "calibrated_power",
                                 "baseline", "baseline_subtracted_power"])
                writer.writerows(csv_rows)
        plt.show(block=False)
        return figure

    def display_lv(self, obs, output_path: str, contour_levels=None, contour_smoothing=2., *, vmin=None, vmax=None):
        """Show/save longitude–LSRK velocity columns from the last LSRK plot.

        Reuse its exact baseline-subtracted, unscaled powers and channel masks.
        Target sky coordinates take precedence over the resolved scan direction.
        Longitude edges are midpoints between observed longitudes, with half a
        spacing at either end (one degree total width for a single longitude).
        Columns retain native velocity channels, without interpolation; the
        view is cropped to the intersection of their valid velocity edges. Separate
        frequency setups and repeated longitudes get separate panels. A common
        white/blue asinh colour mapping preserves amplitude comparisons throughout.
        Optional vmin/vmax further crop the shared coverage and determine the
        visible colour scale, without changing native spectra or exports.
        Magenta contours interpolate between sampled longitudes for display only;
        their labels remain in calibrated power units. Pass [] to disable them.
        """
        from matplotlib.colors import AsinhNorm
        from matplotlib.ticker import FuncFormatter

        if any(value is not None and not np.isfinite(value) for value in (vmin, vmax)):
            raise ValueError("L–V velocity limits must be finite")
        if vmin is not None and vmax is not None and vmin >= vmax:
            raise ValueError("L–V velocity minimum must be less than maximum")
        if not np.isfinite(contour_smoothing) or contour_smoothing < 0:
            raise ValueError("Contour smoothing must be finite and non-negative")
        if contour_levels is not None and (not np.all(np.isfinite(contour_levels)) or
                any(level <= 0 for level in contour_levels) or np.any(np.diff(contour_levels) <= 0)):
            raise ValueError("L–V contour levels must be finite, positive and strictly increasing")
        spectra = []
        for source in self.lsr_spectra:
            target = obs.get_target_by_index(source["target_id"])
            direction = getattr(target, "sky_coord", None)
            if direction is None:
                direction = source["direction"]
            try:
                galactic = direction.galactic
                longitude, latitude = float(galactic.l.deg), float(galactic.b.deg)
                if not np.all(np.isfinite([longitude, latitude])):
                    raise ValueError("Non-finite Galactic coordinates")
            except (AttributeError, TypeError, ValueError) as exc:
                logger.warning("Skipping L–V target %s, frequency %s: %s",
                               source["target_id"], source["freq_scan"], exc)
                continue
            if len(source["velocity"]) < 2 or not np.any(np.isfinite(source["power"].compressed())):
                continue
            spectra.append({**source, "longitude": round(longitude % 360, 8) % 360, "latitude": latitude})
        if not spectra:
            logger.warning("No L–V plot produced: no prepared LSRK spectra with Galactic coordinates")
            return None

        # Cut at the largest unobserved circular gap, keeping 359°/1° adjacent.
        longitudes = np.unique([item["longitude"] for item in spectra])
        gaps = np.diff(np.r_[longitudes, longitudes[0] + 360])
        start = longitudes[(np.argmax(gaps) + 1) % len(longitudes)]
        for item in spectra:
            item["x"] = start + (item["longitude"] - start) % 360
        centres = np.unique([item["x"] for item in spectra])
        def signed_longitude(value):
            wrapped = (value + 180) % 360 - 180
            return 180. if wrapped == -180 else wrapped
        if len(centres) == 1:
            edges = np.array([centres[0] - .5, centres[0] + .5])
        else:
            edges = np.r_[centres[0] - (centres[1] - centres[0]) / 2,
                          (centres[:-1] + centres[1:]) / 2,
                          centres[-1] + (centres[-1] - centres[-2]) / 2]
        panels = {}
        for item in sorted(spectra, key=lambda s: (s["freq_scan"], s["center_freq"], s["x"], s["target_id"])):
            key = item["freq_scan"], item["center_freq"]
            batches = panels.setdefault(key, [])
            batch = next((batch for batch in batches if all(s["x"] != item["x"] for s in batch)), None)
            if batch is None:
                batch = []
                batches.append(batch)
            batch.append(item)
        groups = [(key, index, batch) for key, batches in panels.items() for index, batch in enumerate(batches)]
        velocity_bounds = []
        for item in spectra:
            velocity = item["velocity"]
            item["velocity_edges"] = np.r_[velocity[0] - (velocity[1] - velocity[0]) / 2,
                                           (velocity[:-1] + velocity[1:]) / 2,
                                           velocity[-1] + (velocity[-1] - velocity[-2]) / 2]
            valid = np.flatnonzero(~np.ma.getmaskarray(np.ma.masked_invalid(item["power"])))
            velocity_bounds.append((item["velocity_edges"][valid[0]], item["velocity_edges"][valid[-1] + 1]))
        lower = max(low for low, high in velocity_bounds)
        upper = min(high for low, high in velocity_bounds)
        lower = max(lower, vmin) if vmin is not None else lower
        upper = min(upper, vmax) if vmax is not None else upper
        if lower >= upper:
            logger.warning("No L–V plot produced: columns have no shared valid velocity coverage within the selected limits")
            return None
        powers = np.concatenate([
            np.ma.masked_invalid(item["power"])[(item["velocity_edges"][1:] > lower) &
                                                 (item["velocity_edges"][:-1] < upper)].compressed()
            for item in spectra])
        if not len(powers):
            logger.warning("No L–V plot produced: no valid samples within the selected velocity limits")
            return None
        high = max(float(np.max(powers)), 1e-12)
        norm = AsinhNorm(linear_width=.1 * high, vmin=0., vmax=high, clip=True)
        cmap = reference_lv_colormap()
        levels = list(contour_levels) if contour_levels is not None else (high * np.array([.05, .1, .2, .4, .8])).tolist()
        figure, axes = plt.subplots(len(groups), 1, num=f"L–V Observation {self.obs_id}",
                                    figsize=(12, 5 * len(groups)), squeeze=False,
                                    sharex=True, sharey=True, layout="constrained", clear=True, facecolor="white")
        for axis, (key, index, batch) in zip(axes[:, 0], groups):
            for item in batch:
                column = np.searchsorted(centres, item["x"])
                velocity = item["velocity"]
                velocity_edges = item["velocity_edges"]
                mesh = axis.pcolormesh(edges[column:column + 2], velocity_edges,
                                       np.ma.masked_invalid(item["power"])[:, None],
                                       cmap=cmap, norm=norm, shading="flat", rasterized=True)
            longitude_start = min(item["x"] for item in batch)
            longitude_end = max(item["x"] for item in batch)
            longitude_range = f"{signed_longitude(longitude_start):.3f}° to {signed_longitude(longitude_end):.3f}°"
            if longitude_start // 360 != longitude_end // 360:
                longitude_range += " (across 0°)"
            latitudes = [item["latitude"] for item in batch]
            repeated = f"; group {index + 1}" if len(panels[key]) > 1 else ""
            axis.set_title(f"Frequency scan {key[0]} ({key[1] / 1e6:.3f} MHz){repeated}\n"
                           f"Galactic longitude {longitude_range}; latitude {min(latitudes):+.3f}° to {max(latitudes):+.3f}°")
            axis.set_ylabel("LSRK velocity [km/s]")
            axis.set_xlim(edges[-1], edges[0])
            axis.set_xticks(centres)
            axis.xaxis.set_major_formatter(FuncFormatter(lambda value, position: f"{signed_longitude(value):g}°"))
            axis.set_facecolor("white")
            axis.axhline(0, color="grey", linestyle=":", linewidth=.6, alpha=.6)
        # Clip the view at the shared cell edges without shifting or regridding.
        axes[0, 0].set_ylim(lower, upper)
        for axis, (key, index, batch) in zip(axes[:, 0], groups):
            # Do not span a known missing longitude in this frequency panel.
            columns = [np.searchsorted(centres, item["x"]) for item in batch]
            runs = np.split(np.arange(len(batch)), np.flatnonzero(np.diff(columns) > 1) + 1)
            for run in runs:
                if len(run) < 2 or not levels:
                    continue
                segment = [batch[i] for i in run]
                grid = segment[0]["velocity"]
                grid = grid[(grid >= lower) & (grid <= upper)]
                if len(grid) < 2:
                    continue
                # Only the contour overlay is resampled, never the coloured columns.
                values = np.ma.masked_invalid(np.column_stack([
                    interpolate_lv_contour_column(item["velocity"], item["power"], grid, contour_smoothing) for item in segment]))
                if values.count() < 2:
                    continue
                panel_levels = [level for level in levels if values.min() < level < values.max()]
                if panel_levels:
                    contours = axis.contour([item["x"] for item in segment], grid, values,
                                            levels=panel_levels, colors="#c849b8", linewidths=.65, alpha=.8)
                    # One label per level avoids dense text on noisy observations.
                    for level, paths in zip(contours.levels, contours.allsegs):
                        paths = [path for path in paths if len(path) > 1]
                        if paths:
                            longest = max(paths, key=len)
                            axis.clabel(contours, levels=[level], manual=[longest[len(longest) // 2]],
                                        inline=True, fontsize=7, fmt=lambda value: f"{value:.3g} a.u.")
        axes[-1, 0].set_xlabel("Galactic longitude [degrees]")
        label = "Calibrated power [a.u.]" if self.lsr_baseline_method == "none" else "Baseline-subtracted calibrated power [a.u.]"
        colour_bar = figure.colorbar(mesh, ax=list(axes[:, 0]), label=label)
        colour_bar.set_ticks(sorted(set([0., high] + [level for level in levels if 0 < level < high])))
        from matplotlib.ticker import FormatStrFormatter
        colour_bar.ax.yaxis.set_major_formatter(FormatStrFormatter("%.3g"))
        figure.suptitle(f"Observation {self.obs_id} — Longitude–Velocity (baseline: {self.lsr_baseline_method})")
        figure.savefig(output_path, dpi=150, bbox_inches="tight")
        plt.show(block=False)
        return figure
