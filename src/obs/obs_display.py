import logging
from typing import Iterable

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.gridspec import GridSpec
from matplotlib.ticker import AutoMinorLocator, MaxNLocator
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
            freq_axis = np.linspace(start_mhz, end_mhz, scan.scan_model.spectral_resolution)
            freq_spans.extend([start_mhz, end_mhz])

            y = scan.mpr if scan.mpr is not None else np.zeros(scan.scan_model.spectral_resolution)
            mpr_flags = getattr(scan, "mpr_flags", None)
            if not isinstance(mpr_flags, np.ndarray) or mpr_flags.shape != y.shape:
                mpr_flags = empty_channel_flags(y.shape)
            usable = valid_channels(y, mpr_flags)
            usable_count = int(np.count_nonzero(usable))
            scan_id_parts = str(scan.scan_model.scan_id).split("-")
            scan_label = f"Scan {'-'.join(scan_id_parts[-2:])}" if len(scan_id_parts) >= 2 else f"Scan {scan.scan_model.scan_id}"
            label = f"{scan_label} ({usable_count}/{y.size} usable)"
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

        if self.freq_min_mhz is not None and self.freq_max_mhz is not None:
            self.ax_freq.set_xlim(self.freq_min_mhz, self.freq_max_mhz)
        elif freq_spans:
            self.ax_freq.set_xlim(min(freq_spans), max(freq_spans))

        x_min, x_max = self.ax_freq.get_xlim()
        if x_min <= 1420.4 <= x_max:
            self.ax_freq.axvline(x=1420.4, color="blue", linestyle="--", label="HI Line")

        self.ax_freq.legend(loc="upper right")

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
            scan_id_parts = str(scan.scan_model.scan_id).split("-")
            label = f"Scan {'-'.join(scan_id_parts[-2:])}" if len(scan_id_parts) >= 2 else f"Scan {scan.scan_model.scan_id}"
            self.ax_vel.plot(time_axis, tpw_sum, color=colours[idx], linewidth=1.5, label=label)
            finite_tpw = tpw_sum[np.isfinite(tpw_sum)]
            if finite_tpw.size > 0:
                mean_tpw = float(np.mean(finite_tpw))
                self.ax_vel.axhline(mean_tpw, color=colours[idx], linestyle="--", linewidth=1.0, alpha=0.9)
            max_secs = max(max_secs, time_axis[-1])

        if max_secs > 0:
            self.ax_vel.set_xlim(1, max_secs)

        self.ax_vel.legend(loc="upper right")

    def display(self):
        """Render the observation display."""
        self._prepare_figure()
        self.fig.suptitle(f"Observation {self.obs_id}", fontsize=12)
        self._plot_frequency_panel()
        self._plot_velocity_panel()
        self.fig.canvas.draw_idle()
        plt.show(block=False)

    def save_integrated_total_power(self, output_path: str) -> str:
        """Save the Integrated Total Power panel as a standalone PNG."""
        output_fig, output_axes = plt.subplots(figsize=(14, 4.5))
        display_axes = self.ax_vel

        try:
            self.ax_vel = output_axes
            self._plot_velocity_panel()
            output_fig.tight_layout()
            output_fig.savefig(output_path, dpi=150, bbox_inches="tight")
        finally:
            self.ax_vel = display_axes
            plt.close(output_fig)

        return output_path
