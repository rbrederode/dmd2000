from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from sdp.scan import Scan

import logging
import os
from pathlib import Path
import warnings

_mpl_cache = Path(os.environ.get("MPLCONFIGDIR", "/tmp/dmd2000-matplotlib"))
_mpl_cache.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(_mpl_cache))

import matplotlib as mpl
import numpy as np
from matplotlib.gridspec import GridSpec
from matplotlib.ticker import MaxNLocator

with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore",
        message="Unable to import Axes3D.*",
        category=UserWarning,
    )
    import matplotlib.pyplot as plt

from util import gen_file_prefix
from util.matplotlib_window import get_figure_visibility
from sdp.channel_mask import (
    DEFAULT_EXCLUDED_FLAGS,
    ChannelFlag,
    channels_with_flag,
    contiguous_regions,
    empty_channel_flags,
    masked_values,
    reconstructed_total_power,
    valid_channels,
)

# Disable automatic window raising (backend-specific)
mpl.rcParams["figure.raise_window"] = False

logger = logging.getLogger(__name__)

FIG_SIZE = (14, 7) # Default figure size for plots
WATERFALL_PERCENTILES = (1.0, 99.0)
WATERFALL_LIMIT_SMOOTHING = 0.25
OVERLAY_FONT_SIZE = 8


class SignalDisplay:
    """Performance-oriented signal display with the same visible behaviour as signal_display.py."""

    def __init__(self, dig_id: str):
        """ Initialize the signal display for a given digitiser ID
            :param dig_id: The digitiser ID to display signals for
        """
        self.is_active = True           # Is this signal display instance active
        self.dig_id = dig_id            # Current digitiser signal being displayed

        self.scan = None                # Current digitiser scan being displayed
        self.load = None                # Current digitiser scan baseline being applied
        self.sec = None                 # Current scan second being displayed

        self.fig = None                 # Figure for the digitiser signal display
        self.sig = [None] * 5           # Axes for the signal subplots    
        self.extent = None              # Extent for the imshow plot
        self.freq_axis = None           # Frequency axis values for the spectrum plots
        self.saved_scan_ids = set()     # Set to track which scan IDs have had their figures saved to avoid redundant saves

        self.gs0 = GridSpec(1, 3, width_ratios=[1, 1, 1], left=0.07, right=0.93, top=0.90, bottom=0.3, wspace=0.2)
        self.gs1 = GridSpec(1, 2, width_ratios=[0.32, 0.68], height_ratios=[1], left=0.07, right=0.93, top=0.20, bottom=0.07, wspace=0.2)

        self._reset_artist_refs() 

    def __str__(self):
        return f"SignalDisplay(dig_id={self.dig_id}, is_active={self.is_active})"

    def _reset_artist_refs(self):
        """Reset cached matplotlib artist references for a new scan lifecycle."""
        self.pwr_im = None
        self.bandpass_im = None
        self.total_power_line = None
        self.spr_line = None
        self.load_line = None
        self.cal_line = None
        self.mpr_line = None
        self.spr_rfi_line = None
        self.load_rfi_line = None
        self.cal_rfi_line = None
        self.mpr_rfi_line = None
        self.bandpass_spans = []
        self.qa_text = None
        self.baseline_line = None
        self.signal_start_line = None
        self.signal_end_line = None
        self.qa_signal_start_line = None
        self.qa_signal_end_line = None
        self.mean_tpwr_line = None
        self.i_bar = None
        self.q_bar = None
        self.sat_33_line = None
        self.sat_66_line = None
        self._waterfall_limits = None

    def _create_figure(self):
        """Create the matplotlib figure and all subplot axes for this digitiser display."""
        self.fig = plt.figure(num=f"Digitiser {self.dig_id}", figsize=FIG_SIZE)

        self.sig = [None] * 5                               # Initialize axes for the subplots
        self.sig[0] = self.fig.add_subplot(self.gs0[0])     # Power spectrum summed per second
        self.sig[1] = self.fig.add_subplot(self.gs0[1])     # Sky signal per second
        self.sig[2] = self.fig.add_subplot(self.gs0[2])     # Waterfall plot
        self.sig[3] = self.fig.add_subplot(self.gs1[0])     # SDR saturation level
        self.sig[4] = self.fig.add_subplot(self.gs1[1])     # Total power timeline

        self.fig.subplots_adjust(top=0.78)
        # Show the GUI window before visibility checks can suppress first refreshes.
        try:
            self.fig.show()
            self.fig.canvas.flush_events()
        except Exception as exc:
            logger.debug(f"Signal display for {self.dig_id} could not show figure window: {exc}")

    def _close_figure(self):
        """Close the figure window if it exists."""
        if self.fig is not None:
            plt.close(num=f"Digitiser {self.dig_id}")

    def _prepare_figure_for_scan(self):
        """Prepare the figure for a new scan by creating or clearing axes and resetting artists."""
        if self.fig is None:
            self._create_figure()
        else:
            for ax in self.sig:
                if ax is not None:
                    ax.cla()

        self._reset_artist_refs()

    def is_visible_figure(self) -> Optional[bool]:
        """ Return whether this signal display figure is visible or hidden. 
            A figure can be active but not visible if another figure window is in focus.
            The signal display is considered visible if its figure window is the key window in the OS
        """
        return get_figure_visibility(self.fig)

    def get_is_active(self) -> bool:
        """Return whether this signal display is active and eligible to update."""
        return self.is_active

    def set_is_active(self, active: bool):
        """Set whether the display is active.

        Parameters:
            active: True to allow updates, False to disable updates and clear scan state.
        """
        self.is_active = active
        if not active:
            self.scan = None
            self.load = None
            self.sec = None

    def _configure_axes(self):
        """Initialise titles, labels, limits, and grid settings for all subplot axes."""
        self.init_pwr_spectrum_axes(self.sig[0], "Power/Sec (SPR,BSL)", self.extent, units="{np.abs(shift fft(signal))**2}")
        self.init_pwr_spectrum_axes(self.sig[1], "Power/Sec (CAL)", self.extent)
        self.init_waterfall_axes(self.sig[2])
        self.init_saturation_axes(self.sig[3])
        self.init_total_power_axes(self.sig[4], self.scan.scan_model.duration)

    def _is_integrated_scan(self) -> bool:
        """Return whether the current scan is a manufactured integrated scan."""
        return (
            self.scan is not None
            and self.scan.scan_model is not None
            and bool(getattr(self.scan.scan_model, "synthesised", False))
        )

    def _create_scan_artists(self):
        """Create persistent artists for the current scan so later updates can mutate them in place."""
        self.spr_line, = self.sig[0].plot([], [], color="red", label="Signal (SPR)")
        self.load_line, = self.sig[0].plot([], [], color="black", label="Load (BSL)")
        self.spr_rfi_line, = self.sig[0].plot([], [], color="tab:red", marker="x", linestyle="none", label="_nolegend_")
        self.load_rfi_line, = self.sig[0].plot([], [], color="black", marker="x", linestyle="none", label="_nolegend_")
        self.sig[0].legend(loc="lower right", fontsize=OVERLAY_FONT_SIZE)

        self.cal_line, = self.sig[1].plot([], [], color="orange", label="Signal (CAL)")
        self.mpr_line, = self.sig[1].plot([], [], color="magenta", linewidth=1.5, label="Mean Power (MPR)")
        self.cal_rfi_line, = self.sig[1].plot([], [], color="tab:red", marker="x", linestyle="none", label="_nolegend_")
        self.mpr_rfi_line, = self.sig[1].plot([], [], color="purple", marker="x", linestyle="none", label="_nolegend_")
        self.baseline_line, = self.sig[1].plot([], [], color="tab:blue", linestyle="--", linewidth=1.5, label="Noise Baseline (MPR)")
        self.baseline_line.set_visible(False)
        self.baseline_line.set_data([], [])
        self.qa_signal_start_line = self.sig[1].axvline(x=0, color="purple", linestyle="--", label="Signal Start (MPR)")
        self.qa_signal_end_line = self.sig[1].axvline(x=0, color="purple", linestyle="--", label="Signal End (MPR)")
        self.qa_text = self.sig[1].text(
            0.02,
            0.98,
            "",
            transform=self.sig[1].transAxes,
            ha="left",
            va="top",
            fontsize=OVERLAY_FONT_SIZE,
        )
        self.sig[1].legend(loc="lower right", fontsize=OVERLAY_FONT_SIZE)

        self.pwr_im = self.sig[2].imshow(
            np.zeros((self.scan.scan_model.duration, self.scan.scan_model.spectral_resolution)),
            aspect="auto",
            extent=self.extent,
        )
        waterfall_cmap = self.pwr_im.get_cmap().with_extremes(bad=(0.65, 0.65, 0.65, 0.35))
        self.pwr_im.set_cmap(waterfall_cmap)
        self.bandpass_im = self.sig[2].imshow(
            np.zeros((self.scan.scan_model.duration, self.scan.scan_model.spectral_resolution, 4)),
            aspect="auto",
            extent=self.extent,
            zorder=2,
        )

        self.i_bar = self.sig[3].bar(0, 0, color="blue", label="_nolegend_")[0]
        self.q_bar = self.sig[3].bar(1, 0, color="orange", label="_nolegend_")[0]
        self.sat_33_line = self.sig[3].axhline(y=33, color="green", linestyle="--", label="_nolegend_")
        self.sat_66_line = self.sig[3].axhline(y=66, color="red", linestyle="--", label="_nolegend_")

        self.total_power_line, = self.sig[4].plot([], [], color="red", label="Total Power (TPW)")
        self.mean_tpwr_line = self.sig[4].axhline(y=0, color="red", linestyle="--", label="_nolegend_")
        self.mean_tpwr_line.set_visible(False)
        self.mean_tpwr_line.set_ydata([np.nan, np.nan])
        self.sig[4].legend(loc="lower right", fontsize=OVERLAY_FONT_SIZE)

    def set_scan(self, scan: Scan, load: Scan | None):
        """Bind a scan and optional matching load scan to this display and initialise all plot artists.

        Parameters:
            scan: The Scan instance to display.
            load: Optional equivalent load/baseline Scan to overlay on the plots.
        """
        if not self.is_active:
            self._close_figure()    # Close the figure if it exists
            return

        if scan is None:
            logger.warning(f"Signal display for {self.dig_id} cannot set_scan when scan is None")
            return

        if scan.scan_model.dig_id != self.dig_id:
            logger.warning(f"Signal display for {self.dig_id} cannot set_scan for scan from different dig_id {scan.scan_model.dig_id}")
            return

        if load is not None and load.scan_model.dig_id != self.dig_id:
            logger.warning(f"Signal display for {self.dig_id} cannot set_scan for baseline from different dig_id {load.scan_model.dig_id}")
            return

        self.scan = scan
        self.load = load
        self.sec = None

        # Update the figure for the new scan, creating it if it doesn't exist or clearing axes if it does, and reset all artist references
        self._prepare_figure_for_scan()

        # Update the figure suptitle for the new scan
        self.fig.suptitle(
            f"Scan Id: {scan.scan_model.scan_id}, Type: {scan.scan_model.scan_type.name}, Center Freq: {scan.scan_model.center_freq/1e6:.2f} MHz, "
            f"Gain: {scan.scan_model.gain} dB, Sample Rate: {scan.scan_model.sample_rate/1e6:.2f} MHz, Spectral Resolution: {scan.scan_model.spectral_resolution}",
            fontsize=12,
            y=0.96,
        )

        self.extent = [
            (scan.scan_model.center_freq + scan.scan_model.sample_rate / -2) / 1e6,
            (scan.scan_model.center_freq + scan.scan_model.sample_rate / 2) / 1e6,
            scan.scan_model.duration,
            0,
        ]
        self.freq_axis = np.linspace(self.extent[0], self.extent[1], self.scan.scan_model.spectral_resolution)

        self._configure_axes()          # Set axes properties and labels for the new scan
        self._create_scan_artists()     # Create persistent artists for the new scan so later updates can mutate them in place

    def get_scan(self) -> Scan:
        """Return the Scan currently associated with this display."""
        return self.scan

    def set_load(self, load: Scan | None):
        """Update the optional baseline/load scan for the currently displayed sky scan."""
        if load is not None and load.scan_model.dig_id != self.dig_id:
            logger.warning(f"Signal display for {self.dig_id} cannot set_load for baseline from different dig_id {load.scan_model.dig_id}")
            return

        self.load = load

    @staticmethod
    def _flags_for(scan, product: str, values: np.ndarray) -> np.ndarray:
        """Return flags matching a spectrum, supporting scans saved before flags existed."""
        flags = getattr(scan, f"{product}_flags", None) if scan is not None else None
        if not isinstance(flags, np.ndarray) or flags.shape != values.shape:
            return empty_channel_flags(values.shape)
        return flags

    def _set_rfi_markers(self, line, values: np.ndarray, flags: np.ndarray, label: str) -> None:
        """Mark RFI frequencies at the local usable level so spikes do not affect autoscaling."""
        rfi = channels_with_flag(flags, ChannelFlag.RFI_DETECTED) & np.isfinite(values)
        usable = valid_channels(values, flags)
        rfi_indices = np.flatnonzero(rfi)
        usable_indices = np.flatnonzero(usable)
        if rfi_indices.size > 0 and usable_indices.size > 0:
            marker_values = np.interp(rfi_indices, usable_indices, values[usable_indices])
            line.set_data(self.freq_axis[rfi_indices], marker_values)
            line.set_label(label)
        else:
            line.set_data([], [])
            line.set_label("_nolegend_")

    def _update_bandpass_spans(self, flags: np.ndarray) -> None:
        """Shade contiguous bandpass-excluded frequency ranges on spectrum axes."""
        for span in self.bandpass_spans:
            try:
                span.remove()
            except (ValueError, AttributeError):
                pass
        self.bandpass_spans = []

        channels = flags.shape[0]
        if channels == 0:
            return
        excluded = channels_with_flag(flags, ChannelFlag.BANDPASS_EXCLUDED)
        for region_index, (start, end) in enumerate(contiguous_regions(excluded)):
            freq_start = self.extent[0] + (self.extent[1] - self.extent[0]) * start / channels
            freq_end = self.extent[0] + (self.extent[1] - self.extent[0]) * end / channels
            for axes in (self.sig[0], self.sig[1]):
                self.bandpass_spans.append(
                    axes.axvspan(
                        freq_start,
                        freq_end,
                        color="gray",
                        alpha=0.14,
                        label="Bandpass Excluded" if region_index == 0 else "_nolegend_",
                    )
                )

    def _update_spectrum_axes(self, l_sec: int):
        """Update the SPR, BSL, and CAL line plots for the latest loaded second.

        Parameters:
            l_sec: The latest loaded second number for the current scan, starting at 1.
        """
        spr_values = self.scan.spr[l_sec - 1, :]
        spr_flags = self._flags_for(self.scan, "spr", self.scan.spr)[l_sec - 1, :]
        display_exclusions = DEFAULT_EXCLUDED_FLAGS & ~ChannelFlag.BANDPASS_EXCLUDED
        self.spr_line.set_data(
            self.freq_axis,
            masked_values(spr_values, spr_flags, excluded_flags=display_exclusions),
        )
        self._set_rfi_markers(self.spr_rfi_line, spr_values, spr_flags, "RFI (SPR)")
        if self.load is not None and self.load.mpr is not None:
            load_flags = self._flags_for(self.load, "mpr", self.load.mpr)
            self.load_line.set_data(
                self.freq_axis,
                masked_values(self.load.mpr, load_flags, excluded_flags=display_exclusions),
            )
            self.load_line.set_label("Load (BSL)")
            self.load_line.set_visible(True)
            self._set_rfi_markers(self.load_rfi_line, self.load.mpr, load_flags, "RFI (BSL)")
            self.load_rfi_line.set_visible(True)
        else:
            self.load_line.set_data([], [])
            self.load_line.set_label("_nolegend_")
            self.load_line.set_visible(False)
            self.load_rfi_line.set_data([], [])
            self.load_rfi_line.set_label("_nolegend_")
            self.load_rfi_line.set_visible(False)
        cal_values = self.scan.cal[l_sec - 1, :]
        cal_flag_matrix = self._flags_for(self.scan, "cal", self.scan.cal)
        cal_flags = cal_flag_matrix[l_sec - 1, :]
        mpr_flags = self._flags_for(self.scan, "mpr", self.scan.mpr)
        self.cal_line.set_data(
            self.freq_axis,
            masked_values(cal_values, cal_flags, excluded_flags=display_exclusions),
        )
        self.mpr_line.set_data(
            self.freq_axis,
            masked_values(self.scan.mpr, mpr_flags, excluded_flags=display_exclusions),
        )
        self._set_rfi_markers(self.cal_rfi_line, cal_values, cal_flags, "RFI (CAL)")
        self._set_rfi_markers(self.mpr_rfi_line, self.scan.mpr, mpr_flags, "RFI (MPR)")
        self._update_bandpass_spans(cal_flags)

        legend0 = self.sig[0].get_legend()
        if legend0 is not None:
            legend0.remove()
        self.sig[0].legend(loc="lower right", fontsize=OVERLAY_FONT_SIZE)

        legend1 = self.sig[1].get_legend()
        if legend1 is not None:
            legend1.remove()
        self.sig[1].legend(loc="lower right", fontsize=OVERLAY_FONT_SIZE)

        self.sig[0].relim()
        self.sig[0].autoscale_view(scalex=False, scaley=True)
        self.sig[1].relim()
        self.sig[1].autoscale_view(scalex=False, scaley=True)

    def _refresh_mean_power_spectrum(self):
        """Refresh the scan mean-power spectrum overlay on the calibrated-spectrum axis."""
        if self.mpr_line is None:
            return

        mpr_flags = self._flags_for(self.scan, "mpr", self.scan.mpr)
        display_exclusions = DEFAULT_EXCLUDED_FLAGS & ~ChannelFlag.BANDPASS_EXCLUDED
        self.mpr_line.set_data(
            self.freq_axis,
            masked_values(self.scan.mpr, mpr_flags, excluded_flags=display_exclusions),
        )
        self.sig[1].relim()
        self.sig[1].autoscale_view(scalex=False, scaley=True)

    def _update_qa_overlay(self, l_sec: int):
        """Update the QA text box and signal-region marker lines for the latest second.

        Parameters:
            l_sec: The latest loaded second number for the current scan, starting at 1.
        """
        show_qa_legend = self.scan.scan_qa is not None
        mpr_flags = self._flags_for(self.scan, "mpr", self.scan.mpr)
        usable_count = int(np.count_nonzero(valid_channels(self.scan.mpr, mpr_flags)))
        usable_line = f"Usable: {usable_count}/{self.scan.mpr.size} channels"
        cal_flags = self._flags_for(self.scan, "cal", self.scan.cal)[l_sec - 1, :]
        rfi_flagged_count = int(
            np.count_nonzero(channels_with_flag(cal_flags, ChannelFlag.RFI_DETECTED))
        )
        self.baseline_line.set_label("Noise Baseline (MPR)" if show_qa_legend else "_nolegend_")
        self.qa_signal_start_line.set_label("Signal Start (MPR)" if show_qa_legend else "_nolegend_")
        self.qa_signal_end_line.set_label("Signal End (MPR)" if show_qa_legend else "_nolegend_")

        if self.scan.scan_qa is not None:
            cal_qa = self.scan.scan_qa.getQA("cal", l_sec - 1)
            mpr_qa = self.scan.scan_qa.getQA("mpr", l_sec - 1)
            if mpr_qa is not None:
                if mpr_qa.signal_start is not None and mpr_qa.signal_end is not None:
                    sig_start_bin = int(mpr_qa.signal_start)
                    sig_end_bin = int(mpr_qa.signal_end)
                    freq_start = self.extent[0] + (self.extent[1] - self.extent[0]) * sig_start_bin / self.scan.scan_model.spectral_resolution
                    freq_end = self.extent[0] + (self.extent[1] - self.extent[0]) * sig_end_bin / self.scan.scan_model.spectral_resolution
                    self.qa_signal_start_line.set_xdata([freq_start, freq_start])
                    self.qa_signal_end_line.set_xdata([freq_end, freq_end])
                    self.qa_signal_start_line.set_visible(True)
                    self.qa_signal_end_line.set_visible(True)
                else:
                    self.qa_signal_start_line.set_visible(False)
                    self.qa_signal_end_line.set_visible(False)

                if mpr_qa.baseline is not None:
                    self.baseline_line.set_data(self.freq_axis, np.full_like(self.freq_axis, mpr_qa.baseline, dtype=np.float64))
                    self.baseline_line.set_visible(True)
                else:
                    self.baseline_line.set_visible(False)
                    self.baseline_line.set_data([], [])

                qa_lines = [
                    usable_line,
                    f"RFI Frac: {rfi_flagged_count} channels, {cal_qa.rfi_fraction:.2%}"
                    if cal_qa is not None and cal_qa.rfi_fraction is not None else "",
                    f"Baseline: {mpr_qa.baseline:.2f}" if mpr_qa.baseline is not None else "",
                    f"Noise: {mpr_qa.noise_db:.2f} dB" if mpr_qa.noise_db is not None else "",
                    f"Signal (sum): {mpr_qa.signal_pwr_db:.2f} dB" if mpr_qa.signal_pwr_db is not None else "",
                    f"Signal (peak): {mpr_qa.signal_db:.2f} dB" if mpr_qa.signal_db is not None else "",
                    f"SNR: {mpr_qa.snr_db:.2f} dB" if mpr_qa.snr_db is not None else "",
                    f"FWHM: {mpr_qa.fwhm:.2f} bins" if mpr_qa.fwhm is not None else "",
                    f"DR: {mpr_qa.dynamic_range_db:.2f} dB" if mpr_qa.dynamic_range_db is not None else "",
                ]
                self.qa_text.set_text("\n".join(qa_lines))
        else:
            self.qa_text.set_text(f"QA metrics not configured\n{usable_line}")
            self.baseline_line.set_visible(False)
            self.baseline_line.set_data([], [])
            self.qa_signal_start_line.set_visible(False)
            self.qa_signal_end_line.set_visible(False)

        legend1 = self.sig[1].get_legend()
        if legend1 is not None:
            legend1.remove()
        self.sig[1].legend(loc="lower right", fontsize=OVERLAY_FONT_SIZE)

    def _update_waterfall(self):
        """Update the waterfall image contents from the current calibrated scan data."""
        if self.pwr_im is not None:
            cal_flags = self._flags_for(self.scan, "cal", self.scan.cal)
            display_exclusions = DEFAULT_EXCLUDED_FLAGS & ~ChannelFlag.BANDPASS_EXCLUDED
            waterfall = masked_values(
                self.scan.cal,
                cal_flags,
                excluded_flags=display_exclusions,
            )
            waterfall_mask = np.ma.getmaskarray(waterfall).copy()
            loaded_rows = np.asarray(getattr(self.scan, "loaded_secs", []), dtype=bool)
            if loaded_rows.shape == (waterfall.shape[0],):
                unloaded_rows = ~loaded_rows
            else:
                loaded_seconds = min(int(self.scan.get_loaded_seconds()), waterfall.shape[0])
                unloaded_rows = np.arange(waterfall.shape[0]) >= loaded_seconds
            waterfall_mask[unloaded_rows, :] = True
            self.pwr_im.set_data(np.ma.array(self.scan.cal, mask=waterfall_mask, copy=False))

            # Derive the colour scale from science-usable samples only. The
            # display mask deliberately leaves bandpass channels visible under
            # their grey overlay, but those channels must not influence the
            # waterfall contrast.
            limit_mask = ~valid_channels(self.scan.cal, cal_flags)
            limit_mask |= np.broadcast_to(unloaded_rows[:, None], waterfall.shape)
            limit_values = np.asarray(self.scan.cal)[~limit_mask]
            limits = self._percentile_limits(limit_values)
            if limits is not None:
                observation_complete = not np.any(unloaded_rows)
                if self._waterfall_limits is None or observation_complete:
                    self._waterfall_limits = limits
                else:
                    alpha = WATERFALL_LIMIT_SMOOTHING
                    self._waterfall_limits = tuple(
                        previous + alpha * (current - previous)
                        for previous, current in zip(self._waterfall_limits, limits)
                    )
                self.pwr_im.set_norm(
                    mpl.colors.Normalize(
                        vmin=self._waterfall_limits[0],
                        vmax=self._waterfall_limits[1],
                        clip=True,
                    )
                )

            if self.bandpass_im is not None:
                bandpass = channels_with_flag(cal_flags, ChannelFlag.BANDPASS_EXCLUDED)
                bandpass &= ~waterfall_mask
                overlay = np.zeros((*self.scan.cal.shape, 4), dtype=np.float32)
                overlay[bandpass] = (0.5, 0.5, 0.5, 0.28)
                self.bandpass_im.set_data(overlay)

    @staticmethod
    def _percentile_limits(values: np.ndarray) -> tuple[float, float] | None:
        """Return robust P1-P99 colour limits, with safe degenerate-data handling."""
        finite = np.asarray(values)[np.isfinite(values)]
        if finite.size == 0:
            return None

        if finite.size >= 2:
            vmin, vmax = np.percentile(finite, WATERFALL_PERCENTILES)
        else:
            vmin = vmax = float(finite[0])

        if not np.isfinite(vmin) or not np.isfinite(vmax) or vmin >= vmax:
            vmin, vmax = float(np.min(finite)), float(np.max(finite))
        if vmin >= vmax:
            padding = max(abs(vmin) * 1e-6, 1e-12)
            vmin -= padding
            vmax += padding
        return float(vmin), float(vmax)

    def _update_saturation_axis(self):
        """Update the SDR saturation bars and legend text for the latest I/Q values."""
        self.i_bar.set_height(self.scan.mean_real)
        self.q_bar.set_height(self.scan.mean_imag)
        self.i_bar.set_label(f"I {self.scan.mean_real:.2e}")
        self.q_bar.set_label(f"Q {self.scan.mean_imag:.2e}")

        legend3 = self.sig[3].get_legend()
        if legend3 is not None:
            legend3.remove()
        self.sig[3].legend(loc="lower right", fontsize=OVERLAY_FONT_SIZE)

    def _update_total_power_axis(self, l_sec: int):
        """Update the total-power timeline and show the mean line once the scan is complete.

        Parameters:
            l_sec: The latest loaded second number for the current scan, starting at 1.
        """
        cal_flags = getattr(self.scan, "cal_flags", None)
        if cal_flags is None:
            cal_flags = empty_channel_flags(self.scan.cal.shape)
        total_power, _, _ = reconstructed_total_power(
            self.scan.cal[:l_sec, :],
            cal_flags[:l_sec, :],
        )
        time_axis = np.arange(l_sec) + 1
        self.total_power_line.set_data(time_axis, total_power)

        if l_sec == self.scan.scan_model.duration:
            finite_total_power = total_power[np.isfinite(total_power)]
            if finite_total_power.size > 0:
                avg_tpwr = float(np.mean(finite_total_power))
                self.mean_tpwr_line.set_visible(True)
                self.mean_tpwr_line.set_ydata([avg_tpwr, avg_tpwr])
                self.mean_tpwr_line.set_label(f"Mean {avg_tpwr:.3e}")
            else:
                self.mean_tpwr_line.set_visible(False)
                self.mean_tpwr_line.set_ydata([np.nan, np.nan])
                self.mean_tpwr_line.set_label("_nolegend_")

            legend4 = self.sig[4].get_legend()
            if legend4 is not None:
                legend4.remove()
            self.sig[4].legend(loc="lower right", fontsize=OVERLAY_FONT_SIZE)
        else:
            self.mean_tpwr_line.set_visible(False)
            self.mean_tpwr_line.set_label("_nolegend_")
            self.mean_tpwr_line.set_ydata([np.nan, np.nan])

        self.sig[4].relim(visible_only=True)
        self.sig[4].autoscale_view(scalex=True, scaley=True)

    def _draw(self, is_visible_fig):
        """Render the figure using an appropriate draw path for the current visibility state.

        Parameters:
            is_visible_fig: True if this figure is the active window, False if not, or None if unknown.
        """
        if is_visible_fig is None:
            plt.draw()
            plt.pause(0.0001)
        elif is_visible_fig is True:
            self.fig.canvas.draw()
            self.fig.canvas.flush_events()
        else:
            self.fig.canvas.draw_idle()
            self.fig.canvas.flush_events()

    def _refresh_to_second(self, l_sec: int):
        """Update all artists so the figure reflects the provided loaded second."""
        self._refresh_mean_power_spectrum()
        self._update_waterfall()
        self._update_spectrum_axes(l_sec)
        self._update_qa_overlay(l_sec)
        self._update_saturation_axis()
        self._update_total_power_axis(l_sec)
        self.sec = l_sec

    def display(self):
        """Refresh all plots for the current scan if new seconds have arrived since the last update."""

        # If the signal display is not active
        if not self.is_active:
            self._close_figure()    # Close the figure if it exists
            return

        # If no scan or no figure, then log warning and return
        if self.scan is None or self.fig is None:
            logger.warning(f"Signal display for {self.dig_id} cannot display when {'scan' if self.scan is None else 'figure'} is None")
            return

        # Check if the signal display figure is still visible
        is_visible_fig = self.is_visible_figure()
        if is_visible_fig is False:
            return

        # Get the number of loaded seconds in the scan (starts at 1...scan.duration)
        l_sec = self.scan.get_loaded_seconds()
        # If no seconds are loaded in the scan or the current displayed scan second is the same as loaded scan seconds, return
        if l_sec <= 0:
            return

        # If the current displayed scan second needs to be updated
        if self.sec != l_sec:
    
            logger.debug(f"Signal display updating for scan {self.scan.scan_model.scan_id}, from temporal bin {self.sec} to {l_sec} of {self.scan.scan_model.duration}")

            if self.sec is None:
                self.sec = 0

            self._refresh_to_second(l_sec)
        else:
            self._refresh_mean_power_spectrum()
        
        self._draw(is_visible_fig)

    def save_scan_figure(self, output_dir: str) -> bool:
        """Save the current figure to disk once for the current scan.

        Parameters:
            output_dir: Directory in which to save the signal figure PNG.

        Returns:
            True if a new file was saved, otherwise False.
        """
        if self.scan is None or self.fig is None:
            return False

        l_sec = self.scan.get_loaded_seconds()
        if l_sec <= 0:
            return False

        if self.sec != l_sec:
            logger.debug(
                f"Signal display forcing final refresh for scan {self.scan.scan_model.scan_id} "
                f"from second {self.sec} to {l_sec} before saving figure"
            )
            self._refresh_to_second(l_sec)
        else:
            self._refresh_mean_power_spectrum()

        scan_id = self.scan.scan_model.scan_id
        if scan_id in self.saved_scan_ids:
            return False

        if output_dir is None or output_dir == "":
            output_dir = "."

        prefix = gen_file_prefix(
            dt=self.scan.scan_model.read_start,
            entity_id=self.scan.scan_model.dig_id,
            gain=self.scan.scan_model.gain,
            duration=self.scan.scan_model.duration,
            sample_rate=self.scan.scan_model.sample_rate,
            center_freq=self.scan.scan_model.center_freq,
            spectral_resolution=self.scan.scan_model.spectral_resolution,
            instance_id=self.scan.scan_model.scan_id,
            scan_type=self.scan.scan_model.scan_type,
            filetype="sigfig",
        )

        filename = f"{output_dir}/{prefix}.png"
        # Expand user (~) and ensure the directory exists before saving the figure
        filepath = Path(filename).expanduser()
        filepath.parent.mkdir(parents=True, exist_ok=True)

        self.fig.canvas.draw()
        self.fig.savefig(str(filepath))
        self.saved_scan_ids.add(scan_id)
        logger.info(f"Signal display scan {self.scan.scan_model.scan_id} figure saved to {filepath}")
        return True

    def init_pwr_spectrum_axes(self, axes, title, extent, units="[a.u.]"):
        """ Initialise pwr spectrum plot axes with titles, labels, limits, and grid.

        Parameters:
            axes: Matplotlib axis to configure.
            title: Axis title text.
            extent: Frequency extent list used to set x limits.
            units: Y-axis units label suffix.
        """
        axes.set_title(title)
        axes.set_xlabel("Frequency [MHz]")
        axes.set_ylabel("Power Spectrum" + f" {units}")
        axes.set_xlim(extent[0], extent[1])
        axes.grid(True)

    def init_waterfall_axes(self, axes):
        """Initialise the waterfall subplot axis.

        Parameters:
            axes: Matplotlib axis to configure.
        """
        axes.set_title("Waterfall Plot of Spectrum (P1-P99)")
        axes.set_xlabel("Frequency [MHz]")
        axes.set_ylabel("Integrated Scans" if self._is_integrated_scan() else "Time [sec]")
        axes.set_aspect("auto")
        axes.set_facecolor("black")
        axes.grid(False)
        axes.yaxis.set_major_locator(MaxNLocator(integer=True))

    def init_saturation_axes(self, axes):
        """Initialise the SDR saturation subplot axis.

        Parameters:
            axes: Matplotlib axis to configure.
        """
        axes.set_title("SDR Saturation Level")
        axes.set_xlabel("Mean(I), Mean(Q)")
        axes.set_ylabel("Saturation [%]")
        axes.set_ylim(0, 100)
        axes.set_facecolor("white")
        axes.grid(True)

    def init_total_power_axes(self, axes, duration):
        """Initialise the total-power timeline axis.

        Parameters:
            axes: Matplotlib axis to configure.
            duration: Scan duration in seconds, used to set x limits.
        """
        axes.set_title("Total Power Timeline")
        axes.set_xlabel("Integrated Scans" if self._is_integrated_scan() else "Time [sec]")
        axes.set_ylabel("Total Power [a.u.]")
        axes.set_facecolor("white")
        axes.set_xlim(1, duration)
        axes.grid(True)
        axes.xaxis.set_major_locator(MaxNLocator(integer=True))
