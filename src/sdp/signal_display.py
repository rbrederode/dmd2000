from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sdp.scan import Scan

import logging
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.gridspec import GridSpec
from matplotlib.ticker import MaxNLocator

from util import gen_file_prefix

# Disable automatic window raising (backend-specific)
mpl.rcParams["figure.raise_window"] = False

try:
    from AppKit import NSApplication

    HAS_APPKIT = True
except ImportError:
    HAS_APPKIT = False
    print("AppKit not available. Install pyobjc: pip install pyobjc")

logger = logging.getLogger(__name__)

FIG_SIZE = (14, 7) # Default figure size for plots


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
        self.total_power_line = None
        self.spr_line = None
        self.load_line = None
        self.cal_line = None
        self.mpr_line = None
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

    def is_visible_figure(self) -> bool:
        """ Return whether this signal display figure is visible or hidden. 
            A figure can be active but not visible if another figure window is in focus.
            The signal display is considered visible if its figure window is the key window in the OS
        """
        if not HAS_APPKIT or self.fig is None:
            logger.warning(
                f"Signal display checking whether figure for {self.dig_id} is visible but "
                + ("AppKit not available" if not HAS_APPKIT else "figure is None")
            )
            return None

        key_window = NSApplication.sharedApplication().keyWindow()
        if key_window is None:
            return None

        key_window_title = key_window.title()
        fig_title = self.fig.canvas.manager.get_window_title()
        return fig_title == key_window_title

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
        self.sig[0].legend(loc="lower right")

        self.cal_line, = self.sig[1].plot([], [], color="orange", label="Signal (CAL)")
        self.mpr_line, = self.sig[1].plot([], [], color="magenta", linewidth=1.5, label="Mean Power (MPR)")
        self.baseline_line, = self.sig[1].plot([], [], color="tab:blue", linestyle="--", linewidth=1.5, label="Noise Baseline (MPR)")
        self.baseline_line.set_visible(False)
        self.baseline_line.set_data([], [])
        self.qa_signal_start_line = self.sig[1].axvline(x=0, color="purple", linestyle="--", label="Signal Start (MPR)")
        self.qa_signal_end_line = self.sig[1].axvline(x=0, color="purple", linestyle="--", label="Signal End (MPR)")
        self.qa_text = self.sig[1].text(0.02, 0.98, "", transform=self.sig[1].transAxes, ha="left", va="top")
        self.sig[1].legend(loc="lower right")

        self.pwr_im = self.sig[2].imshow(
            np.zeros((self.scan.scan_model.duration, self.scan.scan_model.channels)),
            aspect="auto",
            extent=self.extent,
        )

        self.i_bar = self.sig[3].bar(0, 0, color="blue", label="_nolegend_")[0]
        self.q_bar = self.sig[3].bar(1, 0, color="orange", label="_nolegend_")[0]
        self.sat_33_line = self.sig[3].axhline(y=33, color="green", linestyle="--", label="_nolegend_")
        self.sat_66_line = self.sig[3].axhline(y=66, color="red", linestyle="--", label="_nolegend_")

        self.total_power_line, = self.sig[4].plot([], [], color="red", label="Total Power (TPW)")
        self.mean_tpwr_line = self.sig[4].axhline(y=0, color="red", linestyle="--", label="_nolegend_")
        self.mean_tpwr_line.set_visible(False)
        self.mean_tpwr_line.set_ydata([np.nan, np.nan])
        self.sig[4].legend(loc="lower right")

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
            f"Gain: {scan.scan_model.gain} dB, Sample Rate: {scan.scan_model.sample_rate/1e6:.2f} MHz, Channels: {scan.scan_model.channels}",
            fontsize=12,
            y=0.96,
        )

        self.extent = [
            (scan.scan_model.center_freq + scan.scan_model.sample_rate / -2) / 1e6,
            (scan.scan_model.center_freq + scan.scan_model.sample_rate / 2) / 1e6,
            scan.scan_model.duration,
            0,
        ]
        self.freq_axis = np.linspace(self.extent[0], self.extent[1], self.scan.scan_model.channels)

        self._configure_axes()          # Set axes properties and labels for the new scan
        self._create_scan_artists()     # Create persistent artists for the new scan so later updates can mutate them in place

    def get_scan(self) -> Scan:
        """Return the Scan currently associated with this display."""
        return self.scan

    def _update_spectrum_axes(self, l_sec: int):
        """Update the SPR, BSL, and CAL line plots for the latest loaded second.

        Parameters:
            l_sec: The latest loaded second number for the current scan, starting at 1.
        """
        self.spr_line.set_data(self.freq_axis, self.scan.spr[l_sec - 1, :])
        if self.load is not None and self.load.mpr is not None:
            self.load_line.set_data(self.freq_axis, self.load.mpr)
            self.load_line.set_label("Load (BSL)")
            self.load_line.set_visible(True)
        else:
            self.load_line.set_data([], [])
            self.load_line.set_label("_nolegend_")
            self.load_line.set_visible(False)
        self.cal_line.set_data(self.freq_axis, self.scan.cal[l_sec - 1, :])
        self.mpr_line.set_data(self.freq_axis, self.scan.mpr)

        legend0 = self.sig[0].get_legend()
        if legend0 is not None:
            legend0.remove()
        self.sig[0].legend(loc="lower right")

        legend1 = self.sig[1].get_legend()
        if legend1 is not None:
            legend1.remove()
        self.sig[1].legend(loc="lower right")

        self.sig[0].relim()
        self.sig[0].autoscale_view(scalex=False, scaley=True)
        self.sig[1].relim()
        self.sig[1].autoscale_view(scalex=False, scaley=True)

    def _refresh_mean_power_spectrum(self):
        """Refresh the scan mean-power spectrum overlay on the calibrated-spectrum axis."""
        if self.mpr_line is None:
            return

        self.mpr_line.set_data(self.freq_axis, self.scan.mpr)
        self.sig[1].relim()
        self.sig[1].autoscale_view(scalex=False, scaley=True)

    def _update_qa_overlay(self, l_sec: int):
        """Update the QA text box and signal-region marker lines for the latest second.

        Parameters:
            l_sec: The latest loaded second number for the current scan, starting at 1.
        """
        show_qa_legend = self.scan.scan_qa is not None
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
                    freq_start = self.extent[0] + (self.extent[1] - self.extent[0]) * sig_start_bin / self.scan.scan_model.channels
                    freq_end = self.extent[0] + (self.extent[1] - self.extent[0]) * sig_end_bin / self.scan.scan_model.channels
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
                    f"RFI Frac: {cal_qa.rfi_fraction:.2%}" if cal_qa is not None and cal_qa.rfi_fraction is not None else "",
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
            self.qa_text.set_text("QA metrics not configured")
            self.baseline_line.set_visible(False)
            self.baseline_line.set_data([], [])
            self.qa_signal_start_line.set_visible(False)
            self.qa_signal_end_line.set_visible(False)

        legend1 = self.sig[1].get_legend()
        if legend1 is not None:
            legend1.remove()
        self.sig[1].legend(loc="lower right")

    def _update_waterfall(self):
        """Update the waterfall image contents from the current calibrated scan data."""
        if self.pwr_im is not None:
            self.pwr_im.set_data(self.scan.cal)
            self.pwr_im.autoscale()

    def _update_saturation_axis(self):
        """Update the SDR saturation bars and legend text for the latest I/Q values."""
        self.i_bar.set_height(self.scan.mean_real)
        self.q_bar.set_height(self.scan.mean_imag)
        self.i_bar.set_label(f"I {self.scan.mean_real:.2e}")
        self.q_bar.set_label(f"Q {self.scan.mean_imag:.2e}")

        legend3 = self.sig[3].get_legend()
        if legend3 is not None:
            legend3.remove()
        self.sig[3].legend(loc="lower right")

    def _update_total_power_axis(self, l_sec: int):
        """Update the total-power timeline and show the mean line once the scan is complete.

        Parameters:
            l_sec: The latest loaded second number for the current scan, starting at 1.
        """
        total_power = np.sum(self.scan.cal[:l_sec, :], axis=1)
        self.total_power_line.set_data(np.arange(1, l_sec + 1), total_power)

        if l_sec == self.scan.scan_model.duration:
            avg_tpwr = np.mean(np.sum(self.scan.cal, axis=1))
            self.mean_tpwr_line.set_visible(True)
            self.mean_tpwr_line.set_ydata([avg_tpwr, avg_tpwr])
            self.mean_tpwr_line.set_label(f"Mean {avg_tpwr:.3e}")

            legend4 = self.sig[4].get_legend()
            if legend4 is not None:
                legend4.remove()
            self.sig[4].legend(loc="lower right")
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
    
            logger.debug(f"Signal display updating for scan {self.scan.scan_model.scan_id}, from second {self.sec} to {l_sec} of {self.scan.scan_model.duration}")

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
            channels=self.scan.scan_model.channels,
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
        axes.set_title("Waterfall Plot of Spectrum")
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
